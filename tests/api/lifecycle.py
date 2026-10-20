"""Helpers for member lifecycle tests (SPEC §9.3, §9.4)."""

from sqlalchemy import select

from app.models import Employee, LitellmWrite

A1, A2 = 1, 2  # D1 groups; A1 holds dana (head), dani, yael; A2 holds noa (head), avi
DANI, DANA, YAEL, AVI = (f"{u}@corp.example" for u in ("dani", "dana", "yael", "avi"))


async def act(client, group_id, upn, action, body=None, reason="why", key=None, **headers):
    """POST /groups/{id}/members/{upn}/{action} with CSRF and a reason."""
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
    if reason is not None:
        h["X-Change-Reason"] = reason
    if key:
        h["Idempotency-Key"] = key
    return await client.post(
        f"/groups/{group_id}/members/{upn}/{action}", json=body or {}, headers=h
    )


async def outbox(factory, upn, after_id=0) -> list[LitellmWrite]:
    """The employee's outbox rows newer than ``after_id``, oldest first."""
    async with factory() as s:
        q = select(LitellmWrite).where(
            LitellmWrite.entity == "employee", LitellmWrite.entity_id == upn,
            LitellmWrite.id > after_id,
        ).order_by(LitellmWrite.id)  # fmt: skip
        return list((await s.scalars(q)).all())


async def last_id(factory) -> int:
    async with factory() as s:
        return (
            await s.scalar(select(LitellmWrite.id).order_by(LitellmWrite.id.desc()).limit(1)) or 0
        )


async def employee(factory, upn) -> Employee:
    async with factory() as s:
        return await s.get(Employee, upn)


async def set_status(factory, upn, status) -> None:
    async with factory() as s:
        (await s.get(Employee, upn)).status = status
        await s.commit()
