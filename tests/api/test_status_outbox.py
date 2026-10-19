from sqlalchemy import select

from app.models import LitellmWrite
from app.outbox.worker import drain


async def test_status_reports_unreachable_during_outage(login_wm, provisioned, fake_app):
    client = await login_wm("dani")  # any authenticated user
    r = await client.get("/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["litellm_reachable"] is True and body["last_sync"] is None
    assert body["outbox"] == {"pending": 0, "failed": 0, "oldest_pending_at": None}
    assert "master" not in r.text.lower() and "secret" not in r.text.lower()

    fake_app.state.fake.outage = True
    down = await (await login_wm("dani")).get("/status")  # fresh app: empty cache
    assert down.status_code == 200 and down.json()["litellm_reachable"] is False
    assert (await client.get("/status")).json()["litellm_reachable"] is True  # cached 10 s


async def test_metrics_exposed(api, seeded):
    body = (await api.get("/metrics")).text
    for name in ("wm_outbox_depth", "wm_spend_snapshot_age_seconds", "wm_overallocation_findings"):
        assert name in body


async def test_status_requires_login(api):
    assert (await api.get("/status")).status_code == 401


async def test_retry_failed_row(login_wm, put_budget, provisioned, litellm, prov_settings):
    admin = await login_wm("admin")
    assert (await put_budget(admin, 1, 1100000)).status_code == 200
    async with provisioned() as s:
        row = (await s.scalars(select(LitellmWrite).where(LitellmWrite.status == "pending"))).one()
        row.status, row.last_error = "failed", "boom"
        row_id = row.id
        await s.commit()

    listed = (await admin.get("/outbox?status=failed")).json()
    assert [r["id"] for r in listed] == [row_id] and listed[0]["last_error"] == "boom"
    assert (await admin.get("/outbox?status=pending")).json() == []
    assert (await admin.get("/status")).json()["outbox"]["failed"] == 1

    h = {"X-CSRF-Token": admin.cookies["wm_csrf"], "X-Change-Reason": "retry"}
    for who in ("rep", "dana"):
        other = await login_wm(who)
        h2 = {**h, "X-CSRF-Token": other.cookies["wm_csrf"]}
        assert (await other.post(f"/outbox/{row_id}/retry", headers=h2)).status_code == 403
        assert (await other.get("/outbox")).status_code == 403
    assert (await admin.post("/outbox/99999/retry", headers=h)).status_code == 404
    r = await admin.post(f"/outbox/{row_id}/retry", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "pending"

    await drain(provisioned, litellm, prov_settings)
    assert (await admin.get("/outbox?status=failed")).json() == []
    assert (await admin.get("/outbox?status=applied")).json() != []
