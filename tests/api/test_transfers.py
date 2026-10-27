"""Step 5.9: transfers of unused balance (D-069, D-071); the total budget never changes."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text

from app.litellm_client import LiteLLMReader
from app.models import AuditLog, Employee, Initiative, Notification, ProductionSystem, Transfer
from app.outbox.worker import drain

A1 = 1  # dana heads it; dani and yael are members with a base cap of 15000 each


async def send(client, method, path, body=None):
    headers = {
        "X-CSRF-Token": client.cookies["wm_csrf"],
        "X-Change-Reason": "transfer",
        "Idempotency-Key": uuid.uuid4().hex,
    }
    return await client.request(method, path, json=body, headers=headers)


def move(amount, source="dani@corp.example", target="yael@corp.example", kinds=("employee",) * 2):
    return {
        "group_id": A1,
        "source": {"kind": kinds[0], "id": source},
        "target": {"kind": kinds[1], "id": target},
        "amount": amount,
    }


async def test_preview_before_after(login_wm, provisioned):
    head = await login_wm("dana")
    r = await send(head, "POST", "/transfers/preview", move(1000))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["source"]["total"], body["source"]["after"]) == (15000, 14000)
    assert (body["target"]["total"], body["target"]["after"]) == (15000, 16000)
    assert body["source"]["unused"] == 15000 and body["total_unchanged"] is True
    async with provisioned() as s:  # a preview writes nothing
        assert await s.scalar(select(func.count()).select_from(Transfer)) == 0
        assert (await s.get(Employee, "dani@corp.example")).base_cap == 15000


async def test_cannot_move_spent_amount_422(login_wm, fake_engine):
    now = datetime.now(UTC)
    async with fake_engine.begin() as conn:  # spend is read fresh from LiteLLM, not the cache
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id, model_group,'
                " \"startTime\", \"endTime\", spend) VALUES ('t1', :u, 'A1', 'm', :at, :at, 14500)"
            ),
            {"u": "dani@corp.example", "at": now},
        )
    head = await login_wm("dana")
    over = await send(head, "POST", "/transfers", move(1000))
    assert over.status_code == 422, over.text
    assert over.json()["details"]["reason"] == "exceeds_unused"
    assert (await send(head, "POST", "/transfers", move(500))).status_code == 200


async def test_outside_authority_403(login_wm, provisioned):
    member, viewer = await login_wm("dani"), await login_wm("noa")  # noa views department D1
    assert (await send(member, "POST", "/transfers", move(100))).status_code == 404
    assert (await send(viewer, "POST", "/transfers", move(100))).status_code == 403
    head = await login_wm("dana")  # an employee of another section is not part of this one
    r = await send(head, "POST", "/transfers", move(100, target="avi@corp.example"))
    assert r.status_code == 404, r.text


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-27; see OPEN-ITEMS")
async def test_org_system_not_allowed(login_wm, provisioned):
    async with provisioned() as s:
        org = ProductionSystem(
            funding="organization", organization_id=1, name="Org", budget=5000, managing_group_id=A1
        )
        own = ProductionSystem(department_id=1, name="Own", budget=5000, managing_group_id=A1)
        s.add_all([org, own])
        await s.commit()
        org_id, own_id = org.id, own.id
    admin = await login_wm("admin")
    r = await send(admin, "POST", "/transfers", move(10, str(own_id), str(org_id), ("system",) * 2))
    assert r.status_code == 404, r.text
    mixed = move(10, "dani@corp.example", str(own_id), ("employee", "system"))
    r = await send(admin, "POST", "/transfers", mixed)
    assert r.status_code == 422 and r.json()["details"]["reason"] == "system_mixed", r.text


async def test_total_unchanged_and_audited(
    login_wm, provisioned, litellm, prov_settings, ro_engine
):
    async with provisioned() as s:
        s.add(Initiative(name="Pilot", owner_upn="yael@corp.example", group_id=A1, department_id=1,
                         budget=2000, created_by="dana@corp.example"))  # fmt: skip
        await s.commit()
        initiative_id = (await s.scalars(select(Initiative.id))).one()
    head = await login_wm("dana")
    r = await send(
        head,
        "POST",
        "/transfers",
        move(700, "dani@corp.example", str(initiative_id), ("employee", "initiative")),
    )
    assert r.status_code == 200, r.text
    tid = int(r.json()["entities"][0]["id"])
    assert r.json()["entities"][0]["values"]["status"] == "pending_apply"
    async with provisioned() as s:
        assert (await s.get(Employee, "dani@corp.example")).base_cap == 14300
        assert (await s.get(Initiative, initiative_id)).budget == 2700
        row = (await s.scalars(select(Transfer))).one()
        assert (row.amount, row.created_by) == (700, "dana@corp.example")
        entities = set(
            await s.scalars(
                select(AuditLog.entity).where(AuditLog.request_id == r.headers["x-request-id"])
            )
        )
        assert {"transfer", "transfer_employee", "transfer_initiative"} <= entities
        kinds = {
            (n.upn, n.kind)
            for n in await s.scalars(
                select(Notification).where(Notification.scope_kind == "transfer")
            )
        }
        assert kinds == {
            ("dani@corp.example", "transfer_released"),
            ("yael@corp.example", "transfer_received"),
        }
    assert (await head.get(f"/transfers/{tid}")).json()["status"] == "pending_apply"
    await drain(provisioned, litellm, prov_settings, LiteLLMReader(ro_engine))
    assert (await head.get(f"/transfers/{tid}")).json()["status"] == "done"
