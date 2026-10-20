import pytest
from sqlalchemy import select

from app.litellm_client import TeamMemberDelete, TeamMemberUpdate, TeamUpdate
from app.models import AuditLog, Employee, LitellmWrite, Notification
from app.outbox.worker import drain
from app.scheduler.jobs import run_reconcile


@pytest.fixture
async def run(provisioned, litellm, fake_clock, prov_settings):
    # Provisioning supplies membership defaults; establish explicit owned values for this scan.
    from app.outbox.desired import build

    async with provisioned() as db:
        for e in await db.scalars(
            select(Employee).where(Employee.status.in_(["active", "blocked"]))
        ):
            if e.group_id:
                await litellm.team_member_update(
                    await build(db, prov_settings, "/team/member_update", e.upn)
                )

    async def call():
        return await run_reconcile(provisioned, litellm, fake_clock, settings=prov_settings)

    return call


async def drift(litellm):
    await litellm.team_update(TeamUpdate(team_id="A1", max_budget=1))


async def test_team_max_budget_drift_corrected_and_logged(run, provisioned, litellm, prov_settings):
    await drift(litellm)
    result = await run()
    assert result.corrected == 1, result.drifts
    async with provisioned() as db:
        row = await db.scalar(select(AuditLog).where(AuditLog.actor_upn == "system:reconcile"))
        assert row.before == {"max_budget": 1}
        assert row.after == {"max_budget": 250000}
    await drain(provisioned, litellm, prov_settings)
    assert (await litellm.team_info("A1")).team_info.max_budget == 250000


async def test_member_temp_fields_drift_corrected_together(
    run, provisioned, litellm, prov_settings
):
    await litellm.team_member_update(
        TeamMemberUpdate(
            team_id="A1",
            user_id="dani@corp.example",
            max_budget_in_team=15000,
            budget_duration="1mo",
            temp_budget_increase=100,
            temp_budget_expiry="2027-01-01T00:00:00Z",
        )
    )
    assert (await run()).corrected == 1
    await drain(provisioned, litellm, prov_settings)
    row = next(
        r
        for r in (await litellm.team_info("A1")).team_memberships
        if r.user_id == "dani@corp.example"
    )
    assert row.litellm_budget_table.temp_budget_increase == 0
    assert row.litellm_budget_table.temp_budget_expiry is None


async def test_key_blocked_and_mapping_follow_status(run, provisioned, litellm, prov_settings):
    async with provisioned() as db:
        e = await db.get(Employee, "dani@corp.example")
        e.status = "blocked"
        await db.commit()
    assert (await run()).corrected == 2
    await drain(provisioned, litellm, prov_settings)
    assert (await litellm.key_list("dani@corp.example")).keys[0].blocked
    assert not next(
        m for m in await litellm.jwt_mapping_list() if m.jwt_claim_value == "dani@corp.example"
    ).is_active


async def test_in_flight_entity_skipped(run, provisioned, litellm):
    await drift(litellm)
    async with provisioned() as db:
        db.add(
            LitellmWrite(entity="group", entity_id="1", endpoint="/team/update", status="failed")
        )
        await db.commit()
    result = await run()
    assert result.skipped_in_flight >= 1 and result.corrected == 0


async def test_no_drift_no_writes(run):
    result = await run()
    assert result.checked > 0 and result.drifts == [] and result.corrected == 0


async def test_repeated_drift_alerts_once_per_day(run, provisioned, litellm, prov_settings):
    for _ in range(4):
        await drift(litellm)
        await run()
        await drain(provisioned, litellm, prov_settings)
    async with provisioned() as db:
        rows = list(
            await db.scalars(select(Notification).where(Notification.kind == "drift_repeated"))
        )
        assert len(rows) == 1 and rows[0].upn == "admin@corp.example"


async def test_missing_membership_reported_not_recreated(run, litellm):
    await litellm.team_member_delete(TeamMemberDelete(team_id="A1", user_id="dani@corp.example"))
    result = await run()
    assert result.missing == 1 and result.corrected == 0


async def test_litellm_down_run_fails_cleanly_no_writes(run, monkeypatch, litellm, provisioned):
    from app.litellm_client import LiteLLMTransientError

    async def down(*args):
        raise LiteLLMTransientError("unavailable")

    monkeypatch.setattr(litellm, "organization_info", down)
    with pytest.raises(LiteLLMTransientError):
        await run()
    async with provisioned() as db:
        assert (
            await db.scalar(select(AuditLog.id).where(AuditLog.actor_upn == "system:reconcile"))
            is None
        )


async def test_run_twice_second_is_noop_after_drain(run, provisioned, litellm, prov_settings):
    await drift(litellm)
    assert (await run()).corrected == 1
    await drain(provisioned, litellm, prov_settings)
    assert (await run()).corrected == 0


async def test_write_during_live_read_is_skipped_even_if_applied(
    run, provisioned, litellm, monkeypatch
):
    original = litellm.team_info

    async def read(team_id):
        info = await original(team_id)
        if team_id == "A1":
            async with provisioned() as db:
                db.add(
                    LitellmWrite(
                        entity="group", entity_id="1", endpoint="/team/update", status="applied"
                    )
                )
                await db.commit()
        return info

    await drift(litellm)
    monkeypatch.setattr(litellm, "team_info", read)
    result = await run()
    assert result.corrected == 0 and result.skipped_in_flight >= 1


async def test_clear_owned_limits_preserves_other_metadata(
    run, provisioned, litellm, prov_settings
):
    from app.litellm_client import KeyUpdate

    await litellm.team_update(
        TeamUpdate(
            team_id="A1",
            tpm_limit=123,
            metadata={"model_tpm_limit": {"extra": 999}, "unowned": "keep"},
        )
    )
    token = (await litellm.key_list("dani@corp.example")).keys[0].token
    await litellm.key_update(KeyUpdate(key=token, model_tpm_limit={"extra": 999}))
    await litellm.team_member_update(
        TeamMemberUpdate(
            team_id="A1",
            user_id="dani@corp.example",
            max_budget_in_team=15000,
            budget_duration="1mo",
            model_max_budget={"extra": {"budget_limit": 10, "time_period": "1mo"}},
        )
    )
    assert (await run()).corrected == 3
    await drain(provisioned, litellm, prov_settings)
    assert (await run()).corrected == 0
    assert (await litellm.team_info("A1")).team_info.metadata["unowned"] == "keep"
    async with provisioned() as db:
        row = await db.scalar(
            select(LitellmWrite)
            .where(
                LitellmWrite.entity == "employee", LitellmWrite.endpoint == "/team/member_update"
            )
            .order_by(LitellmWrite.id.desc())
        )
        assert row.payload["model_max_budget"] == {}
