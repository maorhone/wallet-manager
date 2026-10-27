"""Transfers of unused balance (5.9, D-069/D-071): move credit inside one section's authority.

The total never changes: whatever the source releases the target receives. Only the part of a
source's budget that is neither spent nor committed can move. Internal systems move only among
themselves (they are funded from the department envelope, not from the section's allocation).

Money-movement design (review round 3). Every transfer, its compensation and its settlement
live in this module and change balances only here.
* Lock order, parent -> child: DEPARTMENT, GROUP (the section), PRODUCTION (systems only), then
  the child groups the transfer touches by ascending id. Forward moves (``_plan``) and
  compensation (``_undo``, also from the outbox worker) take exactly this order, with row locks
  (SELECT ... FOR UPDATE) on the balances they rewrite, in the same transaction.
* Lifecycle ``Transfer.state``: pending -> applied (source reduction applied, late-spend fence
  passed) | pending -> compensated (undone), changed only by conditional UPDATEs, so retries,
  outbox redelivery or two workers change balances exactly once. ``failed`` marks a transfer
  that cannot be undone safely (needs an operator).
* Incoming credit counts as pending (not transferable) until the state is ``applied``; all checks
  are aggregate SQL over the full history.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import all_, any_, exists, func, literal, or_, select, update

from app.db import LockKind, lock_parent
from app.errors import ApiError, invalid_request
from app.models import (
    AuditLog,
    Employee,
    Grant,
    GrantShare,
    Grp,
    Initiative,
    LitellmWrite,
    ProductionSystem,
    ServiceKey,
    Transfer,
)
from app.outbox.enqueue import enqueue
from app.permissions import (
    ADMIN,
    HEAD,
    Principal,
    can_write_members,
    group_roles_of,
    target_tier,
)
from app.schemas.transfers import EndpointIn, TransferIn
from app.services import notifications
from app.services.personal_values import authorize
from app.services.production import system_committed
from app.services.service_keys import initiative_committed, initiative_used
from app.services.spend import Spend, SpendUnavailable, fresh_spend
from app.services.validation import check_sum, group_allocated, pending_incoming, settled_budget
from app.writes import WriteContext, plain

ZERO = Decimal(0)
_ENTITY = {"employee": "employee", "group": "group", "system": "production_system"}
_ENDPOINT = {"employee": "/team/member_update", "group": "/team/update"}


@dataclass
class Side:
    kind: str
    id: str
    name: str
    total: Decimal
    committed: Decimal
    row: object

    @property
    def unused(self) -> Decimal:
        return max(self.total - self.committed, ZERO)

    def view(self, delta: Decimal) -> dict:
        return {
            "kind": self.kind, "id": self.id, "name": self.name, "total": self.total,
            "unused": self.unused, "after": self.total + delta,
        }  # fmt: skip

    def set_total(self, value: Decimal) -> None:
        field = {"employee": "base_cap", "group": "base_budget"}.get(self.kind, "budget")
        setattr(self.row, field, value)


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "Entity not found")


async def lock_scope(db, group: Grp, endpoints) -> None:
    """The one lock order (module docstring); used by the forward move and by compensation."""
    await lock_parent(db, LockKind.DEPARTMENT, group.department_id)
    await lock_parent(db, LockKind.GROUP, group.id)
    if any(kind == "system" for kind, _ in endpoints):
        await lock_parent(db, LockKind.PRODUCTION, group.department_id)
    for gid in sorted({int(i) for kind, i in endpoints if kind == "group" and str(i).isdigit()}):
        await lock_parent(db, LockKind.GROUP, gid)


async def group_committed(
    db, spend: Spend, group: Grp, with_shares: bool = False, initiative_spent=None, tz: str = "UTC"
) -> Decimal:
    """Consumption plus what is still allocated and unspent; spent credit is counted once.

    Employee and key spend is part of the group team's own spend, so only the unspent rest of
    each allocation is added. A child group has its own team: its whole carve (or its spend,
    if larger) stays reserved in the parent. ``with_shares`` adds each employee's active grant
    shares to their allocation (the overview's "allocated and unused"; a transfer moves base
    credit only, so its floors leave them out).
    """
    committed = spend.used("group", group.id)
    shares: dict[str, Decimal] = {}
    if with_shares:
        for upn, amount in await db.execute(
            select(GrantShare.upn, GrantShare.amount)
            .join(Grant, Grant.id == GrantShare.grant_id)
            .where(GrantShare.status == "active", Grant.group_id == group.id)
        ):
            shares[upn] = shares.get(upn, ZERO) + amount
    for cap, upn in await db.execute(
        select(Employee.base_cap, Employee.upn).where(
            Employee.group_id == group.id, Employee.status != "left"
        )
    ):
        committed += max(cap + shares.get(upn, ZERO) - spend.used("employee", upn), ZERO)
    for child in await db.scalars(
        select(Grp).where(Grp.parent_group_id == group.id, Grp.status != "archived")
    ):
        committed += max(child.base_budget, spend.used("group", child.id))
    initiatives = {
        i.id: i for i in await db.scalars(select(Initiative).where(Initiative.group_id == group.id))
    }
    # An initiative's consumption comes from ALL its keys, revoked ones included: it is in the
    # group's own spend already, so it must not stay counted as unspent funding (r9 B1a1).
    # ``initiative_used`` also covers keys moved to a system (r9 B3, r10 B1a1); the overview
    # passes its already computed values.
    spent = initiative_spent
    if spent is None:
        spent = {
            i: await initiative_used(db, spend, i, spend.period, tz, window=True)
            for i in initiatives
        }
    for key in await db.scalars(
        select(ServiceKey).where(
            ServiceKey.status.in_(("active", "pending")),
            ServiceKey.owner_kind == "group",
            ServiceKey.owner_id == group.id,
        )
    ):
        committed += max((key.max_budget or ZERO) - spend.used("service_key", key.id), ZERO)
    for i in initiatives.values():
        if i.status == "active":
            committed += max(i.budget - spent.get(i.id, ZERO), ZERO)
    return committed


async def _side(db, spend: Spend, group: Grp, ep: EndpointIn, tz: str = "UTC") -> Side:
    """Load one end, which must belong to ``group``; anything else is invisible (404)."""
    if ep.kind == "employee":
        row = await db.get(Employee, ep.id)
        if row is None or row.group_id != group.id or row.status != "active":
            raise _not_found()
        return Side(
            "employee", row.upn, row.display_name or row.upn, row.base_cap,
            spend.used("employee", row.upn), row,
        )  # fmt: skip
    if not ep.id.isdigit():
        raise _not_found()
    row_id = int(ep.id)
    if ep.kind == "initiative":
        row = await db.get(Initiative, row_id)
        if row is None or row.group_id != group.id or row.status != "active":
            raise _not_found()
        committed = await initiative_committed(db, row_id)
        return Side("initiative", ep.id, row.name, row.budget, committed, row)
    if ep.kind == "group":
        row = await db.get(Grp, row_id)
        if row is None or row.parent_group_id != group.id or row.status == "archived":
            raise _not_found()
        committed = await group_committed(db, spend, row, tz=tz)
        return Side("group", ep.id, row.name, row.base_budget, committed, row)
    row = await db.get(ProductionSystem, row_id)
    if (
        row is None
        or row.funding != "department"
        or row.status != "active"
        or row.managing_group_id != group.id
    ):
        raise _not_found()
    return Side("system", ep.id, row.name, row.budget, await system_committed(db, spend, row), row)


def _canonical(ep: EndpointIn) -> EndpointIn:
    """Numeric ids compare as integers, so "1", "01" and "١" name one entity."""
    if ep.kind != "employee" and ep.id.isdecimal():
        return EndpointIn(kind=ep.kind, id=str(int(ep.id)))
    return ep


async def _plan(
    ctx: WriteContext, principal: Principal, directory, reader, body: TransferIn, clock=None
):
    body = body.model_copy(
        update={"source": _canonical(body.source), "target": _canonical(body.target)}
    )
    group = await ctx.db.get(Grp, body.group_id)
    if group is None:
        raise _not_found()
    await lock_scope(
        ctx.db, group, [(body.source.kind, body.source.id), (body.target.kind, body.target.id)]
    )
    involves_system = "system" in (body.source.kind, body.target.kind)
    if involves_system:
        # Like editing a system budget: an admin or the managing section's head, nobody else.
        roles = await group_roles_of(ctx.db, principal, group.id)
        role = ADMIN if principal.is_admin else HEAD if HEAD in roles else None
        if role is None:
            raise ApiError(403, "FORBIDDEN", "Your role does not permit this action")
        if body.source.kind != body.target.kind:
            raise invalid_request(
                "Internal systems are funded by the department envelope and move only between "
                "systems",
                {"reason": "system_mixed"},
            )
    else:
        role = await can_write_members(ctx.db, principal, group.id)
    now = (clock.now() if clock else datetime.now(ZoneInfo(ctx.settings.app_timezone))).astimezone(
        ZoneInfo(ctx.settings.app_timezone)
    )
    try:  # not the 5-minute UI cache: a missing or stale reading must never count as zero
        spend = await fresh_spend(ctx.db, reader, now)
    except SpendUnavailable:
        raise ApiError(
            503, "SPEND_UNAVAILABLE", "Current usage could not be read; try again shortly"
        ) from None
    source = await _side(ctx.db, spend, group, body.source, ctx.settings.app_timezone)
    target = await _side(ctx.db, spend, group, body.target, ctx.settings.app_timezone)
    if (source.kind, source.id) == (target.kind, target.id):
        raise invalid_request("Source and target are the same", {"reason": "same_entity"})
    for side in (source, target):  # unsettled incoming credit cannot move on, whatever the kind
        if side.kind != "system":  # system_committed already includes it
            side.committed += await pending_incoming(ctx.db, side.kind, side.id)
    for side in (source, target):  # DR9 / FR-G5: the personal-value rules apply to both ends
        if side.kind == "employee":
            tier = await target_tier(ctx.db, directory, side.id)
            role = authorize(principal, side.row, group, tier)
    if body.amount > source.unused:
        raise invalid_request(
            "Only the unused balance can be transferred",
            {"reason": "exceeds_unused", "unused": source.unused},
        )
    return group, role, source, target


async def preview(
    ctx: WriteContext, principal: Principal, directory, reader, body: TransferIn, clock=None
) -> dict:
    _, _, source, target = await _plan(ctx, principal, directory, reader, body, clock)
    return {
        "amount": body.amount,
        "source": source.view(-body.amount),
        "target": target.view(body.amount),
        "total_unchanged": True,
    }


async def _recipients(ctx, directory, side: Side) -> set[str]:
    if side.kind == "employee":
        return {side.id}
    if side.kind == "initiative":
        return {side.row.owner_upn}
    if side.kind == "group":
        return await notifications.heads_of(ctx.db, directory, int(side.id))
    return set()


async def create(
    ctx: WriteContext, principal: Principal, directory, reader, body: TransferIn, clock=None
) -> list[dict]:
    group, role, source, target = await _plan(ctx, principal, directory, reader, body, clock)
    amount = body.amount
    writes = []
    source_write = None
    first_write_id = None
    # Source first: the destination's LiteLLM write waits for the source reduction (D-069).
    for side, delta in ((source, -amount), (target, amount)):
        before = {"total": side.total}
        side.set_total(side.total + delta)
        ctx.audit(
            "transfer_" + side.kind, side.id, before, {"total": side.total + delta}, role,
            group_id=group.id, department_id=group.department_id,
        )  # fmt: skip
        if side.kind in _ENDPOINT or side.kind == "system":
            endpoint = _ENDPOINT.get(side.kind, "/team/update")
            write = await ctx.enqueue(
                _ENTITY[side.kind], side.id, endpoint, depends_on=source_write
            )
            writes.append(write)
            if side is source:
                first_write_id = write
            source_write = source_write or write
    transfer = Transfer(
        group_id=group.id, source_kind=source.kind, source_id=source.id,
        target_kind=target.kind, target_id=target.id, amount=amount,
        write_ids=writes, first_write_id=first_write_id, created_by=principal.upn,
        state="pending" if writes else "applied",
    )  # fmt: skip
    ctx.db.add(transfer)
    await ctx.db.flush()
    values = {
        "source": f"{source.kind}:{source.id}", "target": f"{target.kind}:{target.id}",
        "amount": amount, "status": "pending_apply" if writes else "done",
    }  # fmt: skip
    ctx.audit(
        "transfer", transfer.id, {}, values, role,
        group_id=group.id, department_id=group.department_id,
    )  # fmt: skip
    for side, kind in ((source, "transfer_released"), (target, "transfer_received")):
        await notifications.notify(
            ctx.db, (await _recipients(ctx, directory, side)) - {principal.upn}, kind,
            "transfer", transfer.id, {"transfer_id": transfer.id, "amount": amount},
            f"transfer:{transfer.id}:{kind}", settings=ctx.settings,
        )  # fmt: skip
    return [{"entity": "transfer", "id": transfer.id, "values": values}]


async def transfer_status(db, transfer: Transfer) -> str:
    """done | pending_apply | failed: the lifecycle state first, then the queued writes (D-071).

    ``applied`` needs the source reduction applied AND the fence passed; a compensated or failed
    transfer never reads as done, and an unsettled one (late-spend fence unreadable) stays pending.
    """
    if transfer.state in ("compensated", "failed"):
        return "failed"
    statuses = set(
        await db.scalars(
            select(LitellmWrite.status).where(LitellmWrite.id.in_(transfer.write_ids or []))
        )
    )
    if "failed" in statuses:
        return "failed"
    return "pending_apply" if transfer.state == "pending" else "done"


async def get(db, principal: Principal, transfer_id: int) -> dict:
    transfer = await db.get(Transfer, transfer_id)
    if transfer is None:
        raise _not_found()
    await can_write_members(db, principal, transfer.group_id)
    return {
        "id": transfer.id, "group_id": transfer.group_id, "amount": transfer.amount,
        "source": {"kind": transfer.source_kind, "id": transfer.source_id},
        "target": {"kind": transfer.target_kind, "id": transfer.target_id},
        "status": await transfer_status(db, transfer), "state": transfer.state,
        "created_by": transfer.created_by,
        "created_at": transfer.created_at,
    }  # fmt: skip


async def _transfer_of_source(db, write_id: int) -> Transfer | None:
    return await db.scalar(select(Transfer).where(Transfer.first_write_id == write_id))


async def _undo(db, transfer: Transfer, reason: str) -> bool:
    """Restore both domain balances and cancel the held destination writes, exactly once.

    Locks first (module docstring), then the state claim ``pending -> compensated`` is a
    conditional UPDATE, so a second caller (retry, redelivery, another worker) finds nothing to do.
    """
    group = await db.get(Grp, transfer.group_id)
    ends = [(transfer.source_kind, transfer.source_id), (transfer.target_kind, transfer.target_id)]
    await lock_scope(db, group, ends)
    parents = {}  # a group endpoint may have been moved: its CURRENT parent funds the restore
    for kind, ident in ends:
        if kind == "group":
            moved = await db.get(Grp, int(ident))
            if moved is not None and moved.parent_group_id is not None:
                parents[ident] = moved.parent_group_id
    for pid in sorted(set(parents.values())):
        await lock_parent(db, LockKind.GROUP, pid)
    held = list(
        await db.scalars(
            select(LitellmWrite)
            .where(LitellmWrite.id.in_((transfer.write_ids or [])[1:]))
            .with_for_update()
        )
    )
    if any(w.status not in ("pending", "failed") for w in held):
        return False  # the destination already applied: nothing safe to undo here (a permanently
        # failed write published nothing, so it can be cancelled with its source restored)
    rows = []
    for (kind, ident), delta in zip(ends, (transfer.amount, -transfer.amount), strict=True):
        row = await db.get(
            _MODEL[kind], ident if kind == "employee" else int(ident),
            with_for_update=True, populate_existing=True,
        )  # fmt: skip
        field = {"employee": "base_cap", "group": "base_budget"}.get(kind, "budget")
        rows.append((row, field, getattr(row, field) + delta))
    claim = update(Transfer).where(Transfer.id == transfer.id, Transfer.state == "pending")
    if any(new < 0 for _, _, new in rows) or await _overallocates_parent(
        db, transfer, rows, parents
    ):
        # the target gave the credit away, or returning it would overfill the source's current
        # parent: no balance change, the transfer stays failed for an operator (never silent)
        await db.execute(claim.values(state="failed"))
        return False
    if await db.scalar(claim.values(state="compensated").returning(Transfer.id)) is None:
        return False
    before, after = {}, {}
    for (kind, ident), (row, field, new) in zip(ends, rows, strict=True):
        before[f"{kind}:{ident}"], after[f"{kind}:{ident}"] = getattr(row, field), new
        setattr(row, field, new)
    db.add(
        AuditLog(
            actor_upn="system:compensation",
            actor_role="system",
            entity="transfer_compensation",
            entity_id=str(transfer.id),
            before=plain(before),
            after=plain({**after, "transfer_id": transfer.id, "group_id": transfer.group_id}),
            reason=reason,
            request_id=f"transfer:{transfer.id}:compensation",
        )
    )
    for w in held:
        w.status, w.last_error = "failed", reason
    return True


async def _overallocates_parent(db, transfer, rows, parents) -> bool:
    """Net effect of the restore on each current parent: both ends, whatever their kind."""
    net: dict[int, Decimal] = {}
    for row, field, new in rows:
        if isinstance(row, Grp):
            pid = parents.get(str(row.id))
        elif isinstance(row, Employee | Initiative):
            pid = row.group_id
        else:
            continue  # internal systems are carved from the department, not a group
        if pid is not None:
            net[pid] = net.get(pid, ZERO) + new - getattr(row, field)
    for pid, delta in net.items():
        if delta <= 0:
            continue
        parent = await db.get(Grp, pid, populate_existing=True)
        if check_sum(
            await group_allocated(db, parent) + delta,
            await settled_budget(db, parent),
            parent.overalloc_factor,
        ):
            return True
    return False


async def compensate_failed_source(db, write: LitellmWrite) -> None:
    """The source reduction failed for good: undo the move so no credit exists twice.

    Called by the outbox worker when a write turns ``failed``. The held destination write is
    failed too (it is rebuilt from the restored domain rows if an operator ever retries it).
    A destination without a write (an initiative) is restored as well.
    """
    transfer = await _transfer_of_source(db, write.id)
    if transfer is None or not await _undo(
        db, transfer, f"cancelled: source write {write.id} failed"
    ):
        return
    # an earlier write of the same source may have published this reduction as part of its
    # rebuilt aggregate payload: queue a repair that republishes the restored balance (r8 B1a1)
    key = f"transfer:{transfer.id}:restore-source"
    if await db.scalar(select(LitellmWrite.id).where(LitellmWrite.idempotency_key == key)) is None:
        db.add(
            LitellmWrite(
                entity=write.entity,
                entity_id=write.entity_id,
                endpoint=write.endpoint,
                payload=write.payload,
                idempotency_key=key,
            )  # fmt: skip
        )


async def _source_overspent(
    db, reader, transfer: Transfer, now: datetime, tz: str = "UTC"
) -> bool:
    kind, ident = transfer.source_kind, transfer.source_id
    row = await db.get(_MODEL[kind], ident if kind == "employee" else int(ident))
    total = getattr(row, {"employee": "base_cap", "group": "base_budget"}.get(kind, "budget"))
    spend = await fresh_spend(db, reader, now)  # SpendUnavailable propagates: caller holds
    total -= await unsettled_incoming(db, [kind], ident)  # pending credit never masks spend
    if kind == "group":  # B1a1: consumption plus outstanding unspent allocations, as in planning
        return await group_committed(db, spend, row, tz=tz) > total
    return spend.used(kind, ident) > total


async def _release_cancelled(db, settings, transfer: Transfer) -> None:
    await _undo(
        db, transfer, f"cancelled: source spent the released credit (transfer {transfer.id})"
    )
    if transfer.first_write_id is None:
        return  # no source write: nothing upstream to restore
    source_write = await db.get(LitellmWrite, transfer.first_write_id)
    await enqueue(
        db, settings, source_write.entity, source_write.entity_id, source_write.endpoint,
        f"transfer:{transfer.id}:restore-source",
    )  # fmt: skip


async def _is_source_write(db, row: LitellmWrite) -> bool:
    q = select(Transfer.id).where(Transfer.first_write_id == row.id).limit(1)
    return await db.scalar(q) is not None


async def source_holdback(db, row: LitellmWrite) -> Decimal:
    """B1a1: a source reduction never waits for incoming credit (that would let reciprocal
    transfers wait on each other forever); instead its payload leaves the unsettled incoming
    credit out, so nothing unsettled is published through it. Zero for any other write."""
    if not await _is_source_write(db, row):
        return ZERO
    kinds = [k for k, entity in _ENTITY.items() if entity == row.entity]
    return await unsettled_incoming(db, kinds, row.entity_id)


async def unsettled_incoming(db, kinds, ident) -> Decimal:
    """Incoming credit of a target whose transfer is not settled: never spendable (r7 B1a1)."""
    return await db.scalar(
        select(func.coalesce(func.sum(Transfer.amount), 0)).where(
            Transfer.target_kind.in_(kinds),
            Transfer.target_id == ident,
            Transfer.state.in_(("pending", "failed")),
        )
    )


def apply_holdback(body, hold: Decimal):
    """Take ``hold`` out of the body's own budget field (member cap or team budget)."""
    field = "max_budget_in_team" if hasattr(body, "max_budget_in_team") else "max_budget"
    value = getattr(body, field, None)
    if not hold or value is None:
        return body
    return body.model_copy(update={field: float(Decimal(str(value)) - hold)})


async def held_by_incoming(db, row: LitellmWrite) -> bool:
    """A rebuilt write would publish credit whose source reduction is not applied yet (r3 B1).

    Any write of a transfer's target entity (other than the transfer's own) waits until the
    transfer is ``applied`` (source applied AND fence passed), so no older write can carry the
    incoming credit upstream past an unreadable or failed fence (r5 B1a1).
    """
    if await _is_source_write(db, row):
        return False  # its payload excludes unsettled incoming credit (source_holdback)
    kinds = [k for k, entity in _ENTITY.items() if entity == row.entity]
    # The write is the destination write of one of those transfers: it may follow its siblings'
    # source reductions (no circular wait between receipts), the fence covers their credit.
    own = await db.scalar(
        select(Transfer.id).where(
            literal(row.id) == any_(Transfer.write_ids),
            or_(Transfer.first_write_id.is_(None), Transfer.first_write_id != row.id),
        )
    )
    q = select(Transfer.id).where(
        Transfer.target_kind.in_(kinds),
        Transfer.target_id == row.entity_id,
        Transfer.state.in_(("pending", "failed")),
        literal(row.id) != all_(Transfer.write_ids),
    )
    if own is not None:
        q = q.where(
            Transfer.id != own,
            or_(
                Transfer.state == "failed",
                exists().where(
                    LitellmWrite.id == Transfer.first_write_id, LitellmWrite.status != "applied"
                ),
            ),
        )
    return await db.scalar(q.limit(1)) is not None


async def lock_for_write(db, row: LitellmWrite) -> None:
    """Take the money locks a transfer to ``row``'s entity takes, in the same order (r5 B1a1/B3).

    The worker holds them from the incoming-credit check until its commit, so a transfer cannot
    commit between the check and the payload rebuild. Call BEFORE locking the outbox row.
    """
    if row.entity == "employee":
        gid = await db.scalar(select(Employee.group_id).where(Employee.upn == row.entity_id))
        ends = [("employee", row.entity_id)]
    elif row.entity == "group" and row.entity_id.isdigit():
        gid = await db.scalar(select(Grp.parent_group_id).where(Grp.id == int(row.entity_id)))
        ends = [("group", row.entity_id)]
    elif row.entity == "production_system" and row.entity_id.isdigit():
        gid = await db.scalar(
            select(ProductionSystem.managing_group_id).where(
                ProductionSystem.id == int(row.entity_id)
            )
        )
        ends = [("system", row.entity_id)]
    else:
        return
    group = await db.get(Grp, gid) if gid is not None else None
    if group is not None:
        await lock_scope(db, group, ends)


async def fence_release(db, settings, reader, write: LitellmWrite, now: datetime) -> bool:
    """D-069: before the destination of a transfer is raised, re-read the source's real spend.

    If the source consumed part of the released credit after the transfer was validated, the
    move is undone (source restored upstream by a new write) and the destination is not raised.
    Fails closed: an unreadable spend holds the write. Returns True when it may be applied.
    Destinations without a write (initiatives) get the same check in ``settle``.
    """
    if reader is None:
        return True
    # the write's own transfer, whether or not it has a source write (an initiative-origin
    # receipt has none, so a dependency check would let it bypass an unreadable sibling fence)
    transfer = await db.scalar(
        select(Transfer).where(
            literal(write.id) == any_(Transfer.write_ids),
            or_(Transfer.first_write_id.is_(None), Transfer.first_write_id != write.id),
        )
    )
    if transfer is None:
        return True
    ok = True
    # The rebuilt payload carries every unsettled receipt of this target whose source already
    # applied (or that has no source write), so each of those sources is fenced here too.
    siblings = await db.scalars(
        select(Transfer).where(
            Transfer.target_kind == transfer.target_kind,
            Transfer.target_id == transfer.target_id,
            Transfer.state == "pending",
            Transfer.id != transfer.id,
            or_(
                Transfer.first_write_id.is_(None),
                exists().where(
                    LitellmWrite.id == Transfer.first_write_id, LitellmWrite.status == "applied"
                ),
            ),
        )
    )
    for other in [transfer, *siblings]:
        if await _source_overspent(db, reader, other, now, settings.app_timezone):
            ok = False
            if other.state == "pending":
                await _release_cancelled(db, settings, other)
    return ok


async def _retire_superseded(db, write_id: int) -> list[int]:
    """B1a1: an applied destination write publishes every receipt of its target whose source
    already applied (the payload is rebuilt under the money locks). Those siblings' own
    destination writes are redundant: mark them applied so the transfers settle with the
    payload that actually carried their credit, instead of a later undo restoring only the
    local side. Returns the ids of the transfers whose destination writes were retired."""
    row = await db.get(LitellmWrite, write_id)
    if row is None or row.status != "applied" or row.entity not in _ENTITY.values():
        return []
    is_destination = await db.scalar(
        select(Transfer.id).where(
            literal(write_id) == any_(Transfer.write_ids),
            or_(Transfer.first_write_id.is_(None), Transfer.first_write_id != write_id),
        ).limit(1)
    )  # fmt: skip
    if is_destination is None:
        return []
    kinds = [k for k, entity in _ENTITY.items() if entity == row.entity]
    siblings = await db.scalars(
        select(Transfer).where(
            Transfer.target_kind.in_(kinds),
            Transfer.target_id == row.entity_id,
            Transfer.state == "pending",
            literal(write_id) != all_(Transfer.write_ids),
            or_(
                Transfer.first_write_id.is_(None),  # initiative-origin: no source write (B1a1)
                exists().where(
                    LitellmWrite.id == Transfer.first_write_id, LitellmWrite.status == "applied"
                ),
            ),
        )
    )
    retired = []
    for other in siblings:
        for wid in other.write_ids or []:
            if wid == other.first_write_id:
                continue  # the source write is its own; only destination writes are superseded
            held = await db.get(LitellmWrite, wid)
            # a permanently failed destination write is superseded too: its credit is published
            if held is not None and held.status in ("pending", "failed"):
                held.status, held.last_error = "applied", f"superseded by write {write_id}"
                retired.append(other.id)
    return retired


async def settle(db, settings, reader, now: datetime, write_id: int | None = None) -> None:
    """Pending transfers whose writes are all applied become ``applied`` (credit usable).

    A destination without its own write (an initiative) is fenced here: the source's fresh
    spend is re-read first, and an unreadable spend leaves the transfer pending.
    """
    q = select(Transfer).where(Transfer.state == "pending")
    if write_id is not None:
        retired = await _retire_superseded(db, write_id)
        q = q.where(or_(literal(write_id) == any_(Transfer.write_ids), Transfer.id.in_(retired)))
    for transfer in list(await db.scalars(q)):
        ids = transfer.write_ids or []
        statuses = set(
            await db.scalars(select(LitellmWrite.status).where(LitellmWrite.id.in_(ids)))
        )
        if not ids or statuses != {"applied"}:
            continue
        if transfer.first_write_id and len(ids) == 1 and reader is not None:
            try:
                if await _source_overspent(db, reader, transfer, now, settings.app_timezone):
                    await _release_cancelled(db, settings, transfer)
                    continue
            except SpendUnavailable:
                continue
        await db.execute(
            update(Transfer)
            .where(Transfer.id == transfer.id, Transfer.state == "pending")
            .values(state="applied")
        )


_MODEL = {
    "employee": Employee,
    "group": Grp,
    "initiative": Initiative,
    "system": ProductionSystem,
}
