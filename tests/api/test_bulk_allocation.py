from sqlalchemy import select

from app.models import AuditLog, Employee, Grp
from app.outbox.worker import drain

A1 = 1  # base 250000; members dana (head), dani, yael at 15000 each
P = f"/groups/{A1}/bulk-allocation"


async def post(client, path, body, reason="bulk"):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], "X-Change-Reason": reason}
    return await client.post(path, json=body, headers=h)


def u(name):
    return f"{name}@corp.example"


async def _cap(provisioned, name):
    async with provisioned() as s:
        return (await s.get(Employee, u(name))).base_cap


async def test_preview_replaces_existing_cap(login, provisioned):
    r = await post(await login("dana"), P + "/preview", {"upns": [u("dani")], "amount": 20000})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"][0]["upn"] == u("dani")
    assert (body["rows"][0]["before"], body["rows"][0]["after"]) == (15000, 20000)
    assert body["rows"][0]["effective_total"] == 20000
    assert body["allocated_after"] == 50000
    assert body["over_allocation_warning"] is False
    assert await _cap(provisioned, "dani") == 15000  # preview writes nothing


async def test_overrides_applied(login, provisioned):
    r = await post(
        await login("dana"),
        P,
        {"upns": [u("dani"), u("yael")], "amount": 20000, "overrides": {u("yael"): 5000}},
    )
    assert r.status_code == 200, r.text
    assert await _cap(provisioned, "dani") == 20000
    assert await _cap(provisioned, "yael") == 5000


async def test_commit_atomic_and_audited(login, provisioned, litellm, prov_settings):
    dana = await login("dana")
    r = await post(dana, P, {"upns": [u("dani"), u("dana")], "amount": 1})
    assert r.status_code == 422 and r.json()["code"] == "SELF_ALLOCATION_FORBIDDEN"
    assert await _cap(provisioned, "dani") == 15000
    r = await post(dana, P, {"all": True, "amount": 10000})
    assert r.status_code == 200, r.text
    assert {e["id"] for e in r.json()["entities"]} == {u("dani"), u("yael")}  # head row skipped
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        n = await s.scalar(
            select(AuditLog).where(AuditLog.entity_id == u("dani")).order_by(AuditLog.id.desc())
        )
        assert n is not None and n.after["base_cap"] == 10000
    assert await _cap(provisioned, "yael") == 10000


async def test_over_allocation_follows_existing_factor(login, provisioned):
    dana = await login("dana")
    body = {"upns": [u("dani")], "amount": 250000}
    r = await post(dana, P + "/preview", body)
    assert r.json()["over_allocation_warning"] is True and r.json()["exceeds_limit"] is True
    assert (await post(dana, P, body)).status_code == 422
    async with provisioned() as s:
        (await s.get(Grp, A1)).overalloc_factor = 1.2
        await s.commit()
    r = await post(dana, P + "/preview", body)
    assert r.json()["over_allocation_warning"] is True and r.json()["exceeds_limit"] is False
    assert (await post(dana, P, body)).status_code == 200


async def test_needs_upns_xor_all(login):
    r = await post(await login("dana"), P + "/preview", {"amount": 1})
    assert r.status_code == 422
