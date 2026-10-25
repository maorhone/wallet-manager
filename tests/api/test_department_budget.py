async def test_put_budget_pending_then_applied(
    login, put_budget, provisioned, litellm, prov_settings
):
    from decimal import Decimal

    from app.models import Organization
    from app.outbox.worker import drain

    async with provisioned() as s:  # the legacy route now respects the organization envelope
        (await s.get(Organization, 1)).departments_envelope = Decimal(10_000_000)
        await s.commit()
    client = await login("admin")
    r = await put_budget(client, budget=1250000)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    ent = body["entities"][0]
    assert (ent["entity"], ent["id"], ent["sync_status"]) == ("department", "1", "pending")
    assert ent["values"] == {"budget": 1250000, "budget_period": "1mo"}

    await drain(provisioned, litellm, prov_settings)
    assert (await litellm.organization_info("D1")).litellm_budget_table.max_budget == 1250000
    replay = await put_budget(client, budget=1300000, **{"Idempotency-Key": "x"})  # new request
    assert replay.json()["entities"][0]["sync_status"] == "pending"


async def test_rep_cannot_set_budget_403(login, put_budget):
    r = await put_budget(await login("rep"))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


async def test_viewer_write_403(login, put_budget):
    r = await put_budget(await login("viewer"))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


async def test_unknown_department_404_and_negative_budget_422(login, put_budget):
    client = await login("admin")
    assert (await put_budget(client, dept_id=999)).status_code == 404
    r = await put_budget(client, budget=-1)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
