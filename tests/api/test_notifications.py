"""SPEC §7.9 FR-N2, §11.1: /me/notifications, read, and mute preferences."""

from sqlalchemy import func, select

from app.models import AuditLog, Notification
from app.services.notifications import notify

DANI, DANA = "dani@corp.example", "dana@corp.example"


async def add(factory, upn, kind="grant_received", key="k1") -> int:
    async with factory() as s:
        n = await notify(s, [upn], kind, "grant", 7, {"grant_id": 7}, key)
        await s.commit()
        return n


async def put_prefs(client, body):
    return await client.put(
        "/me/notification-prefs", json=body, headers={"X-CSRF-Token": client.cookies["wm_csrf"]}
    )


async def test_list_mark_read_own_only_404_other(login, seeded):
    await add(seeded, DANI, key="a")
    await add(seeded, DANI, "outbox_failed", key="b")
    await add(seeded, DANA, key="c")
    dani, dana = await login("dani"), await login("dana")
    body = (await dani.get("/me/notifications")).json()
    assert body["unread_count"] == 2
    assert [i["kind"] for i in body["items"]] == ["outbox_failed", "grant_received"]
    assert [i["critical"] for i in body["items"]] == [True, False]
    assert body["items"][0]["scope_kind"] == "grant" and body["items"][0]["read_at"] is None
    assert {p["kind"] for p in body["prefs"]} >= {"employee_80", "drift_repeated"}

    first = body["items"][1]["id"]
    h = {"X-CSRF-Token": dani.cookies["wm_csrf"]}
    r = await dani.post(f"/me/notifications/{first}/read", headers=h)
    assert r.status_code == 200 and r.json()["id"] == first and r.json()["read_at"]
    unread = (await dani.get("/me/notifications?unread=true")).json()
    assert [i["id"] for i in unread["items"]] == [body["items"][0]["id"]]
    assert unread["unread_count"] == 1

    other = (await dana.get("/me/notifications")).json()["items"][0]["id"]
    r = await dani.post(f"/me/notifications/{other}/read", headers=h)
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert (await dani.post("/me/notifications/999999/read", headers=h)).status_code == 404
    async with seeded() as s:  # personal UI state: no audit row
        assert await s.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_requires_login(make_client):
    c = await make_client()
    assert (await c.get("/me/notifications")).status_code == 401


async def test_mute_noncritical_suppresses_new(login, seeded):
    dani = await login("dani")
    r = await put_prefs(dani, {"grant_received": True})
    assert r.status_code == 200
    assert next(p for p in r.json() if p["kind"] == "grant_received")["muted"] is True
    assert await add(seeded, DANI, "grant_received", "x") == 0
    assert await add(seeded, DANI, "grant_expiring", "y") == 1
    await put_prefs(dani, {"grant_received": False})
    assert await add(seeded, DANI, "grant_received", "z") == 1
    async with seeded() as s:
        assert await s.scalar(select(func.count()).select_from(Notification)) == 2


async def test_mute_critical_422(login, seeded):
    dani = await login("dani")
    r = await put_prefs(dani, {"employee_100": True})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
    assert await add(seeded, DANI, "employee_100", "k") == 1  # still delivered


async def test_unknown_kind_422(login):
    r = await put_prefs(await login("dani"), {"nope": True})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
    # nothing was applied when one entry is bad
    r = await put_prefs(await login("dani"), {"grant_received": True, "nope": True})
    assert r.status_code == 422
