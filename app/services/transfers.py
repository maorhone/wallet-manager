"""Transfers of unused balance (5.9, D-069/D-071): move credit inside one section's authority.

The total never changes: whatever the source releases the target receives. Only the part of a
source's budget that is neither spent nor committed can move. Internal systems move only among
themselves (they are funded from the department envelope, not from the section's allocation).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import LockKind
from app.errors import ApiError, invalid_request
from app.models import (
    Employee,
    Grp,
    Initiative,
    LitellmWrite,
    ProductionSystem,
    ServiceKey,
    Transfer,
)
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
from app.services.service_keys import initiative_committed
from app.services.spend import Spend, SpendUnavailable, fresh_spend
from app.writes import WriteContext

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


async def group_committed(db, spend: Spend, group: Grp) -> Decimal:
    """Consumption plus what is still allocated and unspent; spent credit is counted once.

    Employee and key spend is part of the group team's own spend, so only the unspent rest of
    each allocation is added. A child group has its own team: its whole carve (or its spend,
    if larger) stays reserved in the parent.
    """
    committed = spend.used("group", group.id)
    for cap, upn in await db.execute(
        select(Employee.base_cap, Employee.upn).where(
            Employee.group_id == group.id, Employee.status != "left"
        )
    ):
        committed += max(cap - spend.used("employee", upn), ZERO)
    for child in await db.scalars(
        select(Grp).where(Grp.parent_group_id == group.id, Grp.status != "archived")
    ):
        committed += max(child.base_budget, spend.used("group", child.id))
    keys = await db.scalars(
        select(ServiceKey).where(
            ServiceKey.status.in_(("active", "pending")),
            ServiceKey.owner_kind.in_(("group", "initiative")),
        )
    )
    initiatives = {
        i.id: i for i in await db.scalars(select(Initiative).where(Initiative.group_id == group.id))
    }
    spent: dict[int, Decimal] = {}
    for key in keys:
        if key.owner_kind == "group" and key.owner_id == group.id:
            committed += max((key.max_budget or ZERO) - spend.used("service_key", key.id), ZERO)
        elif key.owner_kind == "initiative" and key.owner_id in initiatives:
            spent[key.owner_id] = spent.get(key.owner_id, ZERO) + spend.used("service_key", key.id)
    for i in initiatives.values():
        if i.status == "active":
            committed += max(i.budget - spent.get(i.id, ZERO), ZERO)
    return committed


async def _side(db, spend: Spend, group: Grp, ep: EndpointIn) -> Side:
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
        committed = await group_committed(db, spend, row)
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
    # DEPARTMENT before GROUP everywhere: group_roles_of takes the department lock
    await ctx.lock(LockKind.DEPARTMENT, group.department_id)
    await ctx.lock(LockKind.GROUP, group.id)
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
        await ctx.lock(LockKind.DEPARTMENT, group.department_id)
        await ctx.lock(LockKind.PRODUCTION, group.department_id)
    else:
        role = await can_write_members(ctx.db, principal, group.id)
    for ep in (body.source, body.target):
        if ep.kind == "group" and ep.id.isdigit():
            await ctx.lock(LockKind.GROUP, int(ep.id))
    now = (clock.now() if clock else datetime.now(ZoneInfo(ctx.settings.app_timezone))).astimezone(
        ZoneInfo(ctx.settings.app_timezone)
    )
    try:  # not the 5-minute UI cache: a missing or stale reading must never count as zero
        spend = await fresh_spend(ctx.db, reader, now)
    except SpendUnavailable:
        raise ApiError(
            503, "SPEND_UNAVAILABLE", "Current usage could not be read; try again shortly"
        ) from None
    source = await _side(ctx.db, spend, group, body.source)
    target = await _side(ctx.db, spend, group, body.target)
    if (source.kind, source.id) == (target.kind, target.id):
        raise invalid_request("Source and target are the same", {"reason": "same_entity"})
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
            source_write = source_write or write
    transfer = Transfer(
        group_id=group.id, source_kind=source.kind, source_id=source.id,
        target_kind=target.kind, target_id=target.id, amount=amount,
        write_ids=writes, created_by=principal.upn,
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
    """done | pending_apply | failed, from the outbox rows the transfer queued (D-071)."""
    if not transfer.write_ids:
        return "done"
    statuses = set(
        await db.scalars(select(LitellmWrite.status).where(LitellmWrite.id.in_(transfer.write_ids)))
    )
    if "failed" in statuses:
        return "failed"
    return "pending_apply" if "pending" in statuses else "done"


async def get(db, principal: Principal, transfer_id: int) -> dict:
    transfer = await db.get(Transfer, transfer_id)
    if transfer is None:
        raise _not_found()
    await can_write_members(db, principal, transfer.group_id)
    return {
        "id": transfer.id, "group_id": transfer.group_id, "amount": transfer.amount,
        "source": {"kind": transfer.source_kind, "id": transfer.source_id},
        "target": {"kind": transfer.target_kind, "id": transfer.target_id},
        "status": await transfer_status(db, transfer), "created_by": transfer.created_by,
        "created_at": transfer.created_at,
    }  # fmt: skip


async def compensate_failed_source(db, write: LitellmWrite) -> None:
    """The source reduction failed for good: undo the move so no credit exists twice.

    Called by the outbox worker when a write turns ``failed``. The held destination write is
    failed too (it is rebuilt from the restored domain rows if an operator ever retries it).
    """
    for transfer in await db.scalars(select(Transfer).order_by(Transfer.id.desc()).limit(500)):
        ids = transfer.write_ids or []
        if not ids or ids[0] != write.id or transfer.source_kind == "initiative":
            continue
        held = [
            w for w in await db.scalars(select(LitellmWrite).where(LitellmWrite.id.in_(ids[1:])))
        ]
        if not held or any(w.status != "pending" for w in held):
            return
        for kind, ident, delta in (
            (transfer.source_kind, transfer.source_id, transfer.amount),
            (transfer.target_kind, transfer.target_id, -transfer.amount),
        ):
            row = await db.get(_MODEL[kind], ident if kind == "employee" else int(ident))
            field = {"employee": "base_cap", "group": "base_budget"}.get(kind, "budget")
            setattr(row, field, getattr(row, field) + delta)
        for w in held:
            w.status, w.last_error = "failed", f"cancelled: source write {write.id} failed"
        return


_MODEL = {
    "employee": Employee,
    "group": Grp,
    "initiative": Initiative,
    "system": ProductionSystem,
}
