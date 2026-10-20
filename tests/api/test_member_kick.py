from lifecycle import A1, DANI, act, employee, last_id, outbox
from sqlalchemy import func, select

from app.models import AuditLog, LitellmWrite
from app.outbox.worker import drain


async def test_kick_blocks_key_and_mapping_inactive_order(
    login, provisioned, litellm, prov_settings
):
    start = await last_id(provisioned)
    r = await act(await login("dana"), A1, DANI, "kick", reason="left the project")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["entities"][0]["entity"] == "employee" and body["entities"][0]["id"] == DANI
    assert (await employee(provisioned, DANI)).status == "blocked"

    rows = await outbox(provisioned, DANI, start)
    assert [x.endpoint for x in rows] == ["/key/block", "/jwt/key/mapping/update"]
    assert rows[1].depends_on == rows[0].id
    res = await drain(provisioned, litellm, prov_settings)
    assert (res.applied, res.failed) == (2, 0)
    assert {x.status for x in await outbox(provisioned, DANI, start)} == {"applied"}


async def test_restore_reverses(login, provisioned, litellm, prov_settings):
    dana = await login("dana")
    await act(dana, A1, DANI, "kick")
    await drain(provisioned, litellm, prov_settings)
    start = await last_id(provisioned)
    r = await act(dana, A1, DANI, "restore")
    assert r.status_code == 200, r.text
    assert (await employee(provisioned, DANI)).status == "active"
    rows = await outbox(provisioned, DANI, start)
    assert [x.endpoint for x in rows] == ["/key/unblock", "/jwt/key/mapping/update"]
    assert rows[1].payload["is_active"] is True
    assert (await drain(provisioned, litellm, prov_settings)).failed == 0


async def test_kick_twice_409_state_conflict(login, provisioned):
    dana = await login("dana")
    assert (await act(dana, A1, DANI, "kick")).status_code == 200
    r = await act(dana, A1, DANI, "kick")
    assert r.status_code == 409
    assert (r.json()["code"], r.json()["details"]) == (
        "MEMBER_STATE_CONFLICT",
        {"status": "blocked"},
    )
    r = await act(dana, A1, "yael@corp.example", "restore")  # active, so nothing to restore
    assert r.status_code == 409 and r.json()["details"] == {"status": "active"}


async def test_replay_same_idempotency_key_200_no_new_rows(login, provisioned):
    dana = await login("dana")
    first = await act(dana, A1, DANI, "kick", key="k-1")
    assert first.status_code == 200
    async with provisioned() as s:
        counts = [
            await s.scalar(select(func.count()).select_from(t)) for t in (LitellmWrite, AuditLog)
        ]
    again = await act(dana, A1, DANI, "kick", key="k-1")  # would be a 409 without the replay rule
    assert again.status_code == 200
    assert again.json()["request_id"] == first.json()["request_id"]
    async with provisioned() as s:
        assert counts == [
            await s.scalar(select(func.count()).select_from(t)) for t in (LitellmWrite, AuditLog)
        ]


async def test_kick_during_outage_pending_then_applied(
    login, provisioned, litellm, prov_settings, fake_app
):
    start = await last_id(provisioned)
    fake_app.state.fake.outage = True
    r = await act(await login("dana"), A1, DANI, "kick")
    assert r.status_code == 200 and r.json()["status"] == "pending"
    res = await drain(provisioned, litellm, prov_settings)
    assert res.stopped and res.applied == 0
    assert {x.status for x in await outbox(provisioned, DANI, start)} == {"pending"}
    fake_app.state.fake.outage = False
    res = await drain(provisioned, litellm, prov_settings)
    assert (res.applied, res.failed, res.stopped) == (2, 0, False)
