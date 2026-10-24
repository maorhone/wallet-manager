"""Transfers of unused balance (5.9, D-069/D-071): move credit inside one section's authority.

The total never changes: whatever the source releases the target receives. Only the part of a
source's budget that is neither spent nor committed can move. Internal systems move only among
themselves (they are funded from the department envelope, not from the section's allocation).
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.db import LockKind
from app.errors import ApiError, invalid_request
from app.models import Employee, Grp, Initiative, LitellmWrite, ProductionSystem, Transfer
from app.permissions import ADMIN, HEAD, Principal, can_write_members, group_roles_of
from app.schemas.transfers import EndpointIn, TransferIn
from app.services import notifications
from app.services.production import system_committed
from app.services.service_keys import initiative_committed
from app.services.spend import Spend
from app.services.validation import group_allocated
from app.services.views import period_ctx
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
        committed = max(spend.used("group", row_id), await group_allocated(db, row))
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


async def _plan(ctx: WriteContext, principal: Principal, body: TransferIn, clock=None):
    group = await ctx.db.get(Grp, body.group_id)
    if group is None:
        raise _not_found()
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
    if (body.source.kind, body.source.id) == (body.target.kind, body.target.id):
        raise invalid_request("Source and target are the same", {"reason": "same_entity"})
    period = period_ctx(ctx.settings, None, clock.now() if clock else None)["period"]
    spend = await Spend.load(ctx.db, period)
    source = await _side(ctx.db, spend, group, body.source)
    target = await _side(ctx.db, spend, group, body.target)
    if body.amount > source.unused:
        raise invalid_request(
            "Only the unused balance can be transferred",
            {"reason": "exceeds_unused", "unused": source.unused},
        )
    return group, role, source, target


async def preview(ctx: WriteContext, principal: Principal, body: TransferIn, clock=None) -> dict:
    _, _, source, target = await _plan(ctx, principal, body, clock)
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
    ctx: WriteContext, principal: Principal, directory, body: TransferIn, clock=None
) -> list[dict]:
    group, role, source, target = await _plan(ctx, principal, body, clock)
    amount = body.amount
    writes = []
    for side, delta in ((source, -amount), (target, amount)):
        before = {"total": side.total}
        side.set_total(side.total + delta)
        ctx.audit(
            "transfer_" + side.kind, side.id, before, {"total": side.total + delta}, role,
            group_id=group.id, department_id=group.department_id,
        )  # fmt: skip
        if side.kind in _ENDPOINT:
            writes.append(await ctx.enqueue(_ENTITY[side.kind], side.id, _ENDPOINT[side.kind]))
        elif side.kind == "system":
            writes.append(await ctx.enqueue("production_system", side.id, "/team/update"))
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
