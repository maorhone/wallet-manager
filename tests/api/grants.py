"""Helpers for the grant tests: group ids follow seed order (A1=1, A2=2, C1=5)."""

from datetime import UTC, datetime, timedelta

A1, A2, C1 = 1, 2, 5


def when(**delta) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def grant_body(group_id=A1, amount=50000, **extra) -> dict:
    return {
        "group_id": group_id, "amount": amount, "starts_at": when(days=-1),
        "expires_at": when(days=30), "project_ref": "PRJ-1", "purpose": "pilot", **extra,
    }  # fmt: skip


async def send(client, method, path, body=None, reason="grant", **headers):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
    if reason is not None:
        h["X-Change-Reason"] = reason
    return await client.request(method, path, json=body, headers=h)


async def create(client, body=None, **headers):
    return await send(client, "POST", "/grants", body or grant_body(), **headers)


async def shared_grant(login, provisioned, litellm, settings, upn="dani@corp.example"):
    """A grant in A1 with a 3000 share to ``upn``, applied; returns (grant id, share id)."""
    from app.outbox.worker import drain

    gid = int((await create(await login("pmo"))).json()["entities"][0]["id"])
    rows = [{"upn": upn, "amount": 3000, "expires_at": when(days=10)}]
    r = await send(await login("dana"), "POST", f"/grants/{gid}/shares", rows, "share")
    assert r.status_code == 200, r.text
    await drain(provisioned, litellm, settings)
    return gid, int(r.json()["entities"][0]["id"])
