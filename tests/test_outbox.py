import uuid

from sqlalchemy import select

from app.models import AuditLog, Department, Employee, LitellmWrite
from app.outbox.worker import drain
from app.seed import seed
from app.writes import WriteContext


async def _enqueue(factory, settings, entity, entity_id, endpoint):
    async with factory() as s:
        ctx = WriteContext(s, settings, "tester@corp.example", str(uuid.uuid4()), "test")
        await ctx.enqueue(entity, entity_id, endpoint)
        await s.commit()


async def _statuses(factory) -> list[str]:
    async with factory() as s:
        return list((await s.execute(select(LitellmWrite.status).order_by("id"))).scalars())


async def test_drain_applies_and_marks_applied(provisioned, litellm):
    statuses = set(await _statuses(provisioned))
    assert statuses == {"applied"}
    assert (await litellm.organization_info("D1")).litellm_budget_table.max_budget == 1000000
    assert (await litellm.team_info("A1")).team_info.organization_id == "D1"


async def test_outage_keeps_pending_then_applies_after_recovery(
    seeded, prov_settings, litellm, fake_app
):
    fake_app.state.fake.outage = True
    await seed(seeded, prov_settings.seed_file, settings=prov_settings)
    res = await drain(seeded, litellm, prov_settings)
    assert res.stopped and res.applied == 0
    async with seeded() as s:
        rows = (await s.execute(select(LitellmWrite).order_by(LitellmWrite.id))).scalars().all()
    assert {r.status for r in rows} == {"pending"}
    assert rows[0].attempts == 1 and rows[0].last_error and rows[1].attempts == 0
    fake_app.state.fake.outage = False
    res = await drain(seeded, litellm, prov_settings)
    assert not res.stopped and not res.failed
    assert set(await _statuses(seeded)) == {"applied"}


async def test_permanent_error_marks_failed(seeded, prov_settings, litellm):
    # D1's org was never created in LiteLLM, so the update is a 404; the next row still runs.
    await _enqueue(seeded, prov_settings, "department", "1", "/organization/update")
    await _enqueue(seeded, prov_settings, "department", "2", "/organization/new")
    res = await drain(seeded, litellm, prov_settings)
    assert (res.failed, res.applied, res.stopped) == (1, 1, False)
    async with seeded() as s:
        rows = (await s.execute(select(LitellmWrite).order_by(LitellmWrite.id))).scalars().all()
    assert [r.status for r in rows] == ["failed", "applied"]
    assert "404" in rows[0].last_error


async def test_apply_uses_latest_desired_state(seeded, prov_settings, litellm):
    await _enqueue(seeded, prov_settings, "department", "1", "/organization/new")
    async with seeded() as s, s.begin():
        (await s.get(Department, 1)).budget = 777
    await drain(seeded, litellm, prov_settings)
    assert (await litellm.organization_info("D1")).litellm_budget_table.max_budget == 777
    async with seeded() as s:
        assert (await s.scalar(select(LitellmWrite))).payload["max_budget"] == 777


async def test_personal_key_plaintext_never_stored(provisioned, litellm):
    async with provisioned() as s:
        emp = await s.get(Employee, "dana@corp.example")
        assert emp.key_token_hash and not emp.key_token_hash.startswith("sk-")
        texts = [
            str(r.payload) + str(r.last_error) for r in (await s.scalars(select(LitellmWrite)))
        ]
        texts += [str(a.before) + str(a.after) for a in (await s.scalars(select(AuditLog)))]
        texts += [str(e.__dict__) for e in (await s.scalars(select(Employee)))]
    assert not any("sk-" in t for t in texts)
