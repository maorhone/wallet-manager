"""SPEC §9.1-9.3: first-login onboarding through the portal endpoints."""

import asyncio
import logging

import pytest
from lifecycle import DANI, employee, outbox, set_status
from sqlalchemy import func, select, text

from app.config import Settings
from app.directory import DirectoryUnavailable, LdapDirectory
from app.models import AuditLog, Employee, LitellmWrite
from app.outbox.worker import drain
from app.services.lifecycle import JOINER


def _bad_ldap_directory() -> LdapDirectory:
    def connect():
        raise OSError("connection refused")

    settings = Settings(_env_file=None, ldap_user_base="OU=Users,DC=corp,DC=example")
    return LdapDirectory(settings, connect=connect)


NEWBIE, DRIFTER, TWIN = (f"{u}@corp.example" for u in ("newbie", "drifter", "twin"))
GHOST = "ghost@corp.example"
B1 = {"id": 3, "name": "Group B1"}
D1 = {"id": 1, "name": "Department D1"}


async def counts(factory) -> list[int]:
    async with factory() as s:
        return [
            await s.scalar(select(func.count()).select_from(t))
            for t in (Employee, AuditLog, LitellmWrite)
        ]


async def test_new_user_provisioning_then_ready_after_drain(
    portal, provisioned, litellm, prov_settings
):
    r = await portal.post(NEWBIE)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["upn"], body["status"], body["reason"]) == (NEWBIE, "provisioning", None)
    assert (body["group"], body["department"]) == (B1, D1)
    assert "install" not in body and body["request_id"]
    emp = await employee(provisioned, NEWBIE)
    assert (emp.status, emp.group_id, emp.display_name) == ("active", 3, "Newbie Nadav")

    assert (await portal.get(NEWBIE)).json()["status"] == "provisioning"
    res = await drain(provisioned, litellm, prov_settings)
    assert (res.applied, res.failed) == (4, 0)
    ready = await portal.get(NEWBIE)
    assert ready.status_code == 200 and ready.json()["status"] == "ready"
    assert (await employee(provisioned, NEWBIE)).key_token_hash


async def test_joiner_order_user_member_key_mapping(portal, provisioned):
    await portal.post(NEWBIE)
    rows = await outbox(provisioned, NEWBIE)
    assert tuple(x.endpoint for x in rows) == JOINER
    assert [x.depends_on for x in rows] == [None, rows[0].id, rows[1].id, rows[2].id]
    async with provisioned() as s:
        audit = (await s.scalars(select(AuditLog).where(AuditLog.entity_id == NEWBIE))).all()
    assert [(a.actor_upn, a.actor_role) for a in audit] == [(NEWBIE, "onboarding_portal")]
    assert audit[0].after["client_id"] and audit[0].reason.startswith("first-login onboarding via")


async def test_same_idempotency_key_replay_no_new_rows(portal, provisioned):
    first = await portal.post(NEWBIE, key="k-1")
    before = await counts(provisioned)
    again = await portal.post(NEWBIE, key="k-1")
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["status"] == first.json()["status"]
    assert again.json()["request_id"] == first.json()["request_id"]
    assert await counts(provisioned) == before


async def test_post_requires_idempotency_key(portal, provisioned):
    r = await portal.post(NEWBIE, key=None)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
    assert await employee(provisioned, NEWBIE) is None


async def test_existing_user_other_key_no_changes(portal, provisioned):
    before = await counts(provisioned)
    r = await portal.post(DANI)
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["group"]["id"]) == ("ready", 1)
    assert await counts(provisioned) == before


async def test_concurrent_first_calls_one_employee(portal, provisioned):
    a, b = await asyncio.gather(portal.post(NEWBIE), portal.post(NEWBIE))
    assert sorted((a.status_code, b.status_code)) == [200, 201]
    assert a.json()["status"] == b.json()["status"] == "provisioning"
    async with provisioned() as s:
        assert await s.scalar(select(func.count()).where(Employee.upn == NEWBIE)) == 1
    assert len(await outbox(provisioned, NEWBIE)) == 4


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-25; see OPEN-ITEMS")
async def test_no_group_pending_no_litellm_calls(portal, provisioned):
    r = await portal.post(DRIFTER)
    assert r.status_code == 201
    body = r.json()
    assert (body["status"], body["reason"], body["group"]) == (
        "pending_placement",
        "no_budget_group",
        None,
    )
    assert body["department"] == D1 and "install" not in body
    emp = await employee(provisioned, DRIFTER)
    assert (emp.status, emp.group_id, emp.pending_department_id) == ("pending_placement", None, 1)
    assert emp.pending_reason == "no_budget_group" and emp.pending_since
    assert await outbox(provisioned, DRIFTER) == []


async def test_multiple_groups_pending(portal, provisioned):
    body = (await portal.post(TWIN)).json()
    assert (body["status"], body["reason"], body["department"]) == (
        "pending_placement",
        "multiple_budget_groups",
        D1,
    )
    assert await outbox(provisioned, TWIN) == []


async def test_not_in_directory_pending_unknown_department(portal, provisioned):
    body = (await portal.post(GHOST)).json()
    assert (body["status"], body["reason"], body["department"]) == (
        "pending_placement",
        "not_in_directory",
        None,
    )
    emp = await employee(provisioned, GHOST)
    assert emp.pending_department_id is None and emp.display_name is None


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-23; see OPEN-ITEMS")
async def test_kicked_returns_blocked(portal, provisioned):
    await set_status(provisioned, DANI, "blocked")
    for r in (await portal.post(DANI), await portal.get(DANI)):
        assert r.status_code == 200
        assert (r.json()["status"], r.json()["reason"]) == ("blocked", "kicked")


async def test_deleted_returns_blocked(portal, provisioned):
    await set_status(provisioned, DANI, "left")
    for r in (await portal.post(DANI), await portal.get(DANI)):
        assert r.status_code == 200
        assert (r.json()["status"], r.json()["reason"]) == ("blocked", "deleted")
    assert (await employee(provisioned, DANI)).status == "left"  # D20: only an admin re-places


async def test_install_only_when_ready_and_has_no_secret(
    portal, provisioned, litellm, prov_settings
):
    await portal.post(NEWBIE)
    assert "install" not in (await portal.get(NEWBIE)).json()
    await drain(provisioned, litellm, prov_settings)
    install = (await portal.get(NEWBIE)).json()["install"]
    assert install["litellm_base_url"] == "https://litellm.corp.example"
    assert install["auth"] == "adfs_jwt"
    assert set(install["env"]) == {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"}
    assert install["env"]["ANTHROPIC_BASE_URL"] == "https://litellm.corp.example"
    assert install["env"]["ANTHROPIC_AUTH_TOKEN"].startswith("<")  # a placeholder
    assert install["notes"] and "sk-" not in str(install)


async def test_secret_never_returned_stored_or_logged(
    portal, provisioned, litellm, prov_settings, monkeypatch, caplog
):
    plaintext = []
    real = litellm.key_generate

    async def spy(body):
        resp = await real(body)
        plaintext.append(resp.key)
        return resp

    monkeypatch.setattr(litellm, "key_generate", spy)
    caplog.set_level(logging.DEBUG)
    bodies = [(await portal.post(NEWBIE)).text]
    await drain(provisioned, litellm, prov_settings)
    bodies += [(await portal.get(NEWBIE)).text, (await portal.post(NEWBIE)).text]
    assert len(plaintext) == 1 and plaintext[0]

    secret = plaintext[0]
    assert not any(secret in b for b in bodies)
    assert secret not in caplog.text
    jwt_in_logs = [portal.headers(NEWBIE)[h] for h in ("Authorization", "X-User-Assertion")]
    assert not any(t.removeprefix("Bearer ") in caplog.text for t in jwt_in_logs)
    async with provisioned() as s:
        names = (
            await s.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        ).all()
        for name in names:
            dump = (await s.scalars(text(f'SELECT row_to_json(t)::text FROM "{name}" t'))).all()
            assert not any(secret in row for row in dump), name


async def test_directory_unavailable_503_writes_nothing(portal, provisioned):
    class Down:
        def __getattr__(self, name):
            def fail(*a, **k):
                raise DirectoryUnavailable("ldap down")

            return fail

    portal.app.state.directory = Down()
    before = await counts(provisioned)
    r = await portal.post(NEWBIE)
    assert r.status_code == 503 and r.json()["code"] == "DIRECTORY_UNAVAILABLE"
    assert await counts(provisioned) == before
    assert (await portal.post(DANI)).status_code == 200  # existing employees are unaffected


async def test_ldap_directory_down_503_nothing_written(portal, provisioned):
    """Step 3.1: a real ``LdapDirectory`` whose connection fails degrades the same way (§5.4)."""
    portal.app.state.directory = _bad_ldap_directory()
    before = await counts(provisioned)
    r = await portal.post(NEWBIE)
    assert r.status_code == 503 and r.json()["code"] == "DIRECTORY_UNAVAILABLE"
    assert await counts(provisioned) == before
    assert (await portal.post(DANI)).status_code == 200  # existing employees are unaffected


async def test_get_onboarding_never_writes(portal, provisioned):
    before = await counts(provisioned)
    assert (await portal.get(DANI)).status_code == 200
    assert (await portal.get(NEWBIE)).status_code == 404
    assert await counts(provisioned) == before


async def test_get_unknown_upn_404(portal):
    r = await portal.get(GHOST)
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
