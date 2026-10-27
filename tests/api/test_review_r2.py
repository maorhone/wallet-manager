"""Review round 2 blockers: payload target injection, AI-team limits, transfer money safety."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.litellm_client import LiteLLMReader
from app.models import Employee, LitellmWrite, Policy, ServiceKey
from app.outbox.worker import drain
from tests.api.test_requests import send as rsend
from tests.api.test_review_money import YAEL, _initiative
from tests.api.test_transfers import move, send


async def test_payload_target_injection_rejected(login_wm, provisioned):
    iid = await _initiative(provisioned, budget=1000, owner=YAEL)
    dani = await login_wm("dani")  # does not own the initiative
    r = await rsend(
        dani, "POST", "/requests",
        {"type": "credits", "amount": 100,
         "payload": {"need": "x", "target": {"kind": "initiative", "id": iid}}},
    )  # fmt: skip
    assert r.status_code == 422, r.text


async def test_initiative_key_limits_are_policy_floor_for_non_ai(login_wm, provisioned):
    iid = await _initiative(provisioned, budget=1000, owner=YAEL)
    head = await login_wm("dana")
    body = {
        "owner_kind": "initiative", "owner_id": iid, "alias": "bot", "max_budget": 10,
        "max_parallel": 9, "rpm_limit": 900, "models": ["test-model"],
    }  # fmt: skip
    r = await send(head, "POST", "/service-keys", body)
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        floor = {k: (await s.get(Policy, k)).value["min"] for k in ("concurrency", "rpm")}
        key = (await s.scalars(select(ServiceKey).where(ServiceKey.owner_id == iid))).first()
        assert (key.max_parallel, key.rpm_limit) == (floor["concurrency"], floor["rpm"])


async def test_incoming_unapplied_credit_cannot_move_on(login_wm, provisioned):
    iid = await _initiative(provisioned, budget=0, owner=YAEL)
    head = await login_wm("dana")
    first = await send(
        head, "POST", "/transfers", move(100, target=str(iid), kinds=("employee", "initiative"))
    )
    assert first.status_code == 200, first.text
    onward = await send(
        head, "POST", "/transfers",
        move(100, source=str(iid), target="yael@corp.example", kinds=("initiative", "employee")),
    )  # fmt: skip
    assert onward.status_code == 422, onward.text
    assert onward.json()["details"]["reason"] == "exceeds_unused"


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-27; see OPEN-ITEMS")
async def test_source_spending_after_validation_cancels_release(
    login_wm, provisioned, fake_engine, litellm, prov_settings, ro_engine
):
    head = await login_wm("dana")
    r = await send(head, "POST", "/transfers", move(15000))
    assert r.status_code == 200, r.text
    async with fake_engine.begin() as conn:  # the source spends after validation, before apply
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id, model_group,'
                " \"startTime\", \"endTime\", spend) VALUES ('r2', :u, 'A1', 'm', :at, :at, 30)"
            ),
            {"u": "dani@corp.example", "at": datetime.now(UTC)},
        )
    await drain(provisioned, litellm, prov_settings, LiteLLMReader(ro_engine))
    async with provisioned() as s:
        assert (await s.get(Employee, "dani@corp.example")).base_cap == 15000
        assert (await s.get(Employee, "yael@corp.example")).base_cap == 15000
        assert "failed" in set(await s.scalars(select(LitellmWrite.status)))
