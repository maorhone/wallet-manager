"""Helpers for the scheduler tests: grants and shares created through the API."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import LitellmWrite

A1 = 1
DANI, DANA, YAEL = "dani@corp.example", "dana@corp.example", "yael@corp.example"


def when(**delta) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


async def send(client, method, path, body, reason="test"):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], "X-Change-Reason": reason}
    r = await client.request(method, path, json=body, headers=h)
    assert r.status_code == 200, r.text
    return r


async def new_grant(login, amount=50000, starts=-1, expires=30) -> int:
    body = {
        "group_id": A1, "amount": amount, "starts_at": when(days=starts),
        "expires_at": when(days=expires), "project_ref": "PRJ-1", "purpose": "pilot",
    }  # fmt: skip
    r = await send(await login("pmo"), "POST", "/grants", body)
    return int(r.json()["entities"][0]["id"])


async def new_share(login, grant_id, upn=DANI, amount=3000, days=10) -> int:
    rows = [{"upn": upn, "amount": amount, "expires_at": when(days=days)}]
    r = await send(await login("dana"), "POST", f"/grants/{grant_id}/shares", rows)
    return int(r.json()["entities"][0]["id"])


async def membership(litellm, upn=DANI):
    info = await litellm.team_info("A1")
    return next(m for m in info.team_memberships if m.user_id == upn).litellm_budget_table


async def outbox(factory, endpoint, entity_id, after=0) -> list[LitellmWrite]:
    async with factory() as s:
        q = select(LitellmWrite).where(
            LitellmWrite.endpoint == endpoint, LitellmWrite.entity_id == str(entity_id),
            LitellmWrite.id > after,
        ).order_by(LitellmWrite.id)  # fmt: skip
        return list((await s.scalars(q)).all())


async def last_id(factory) -> int:
    async with factory() as s:
        return (
            await s.scalar(select(LitellmWrite.id).order_by(LitellmWrite.id.desc()).limit(1)) or 0
        )
