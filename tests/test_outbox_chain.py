"""Chained outbox rows (SPEC §9.3): ``depends_on``, frozen payloads, delete 404 = applied."""

import uuid

from sqlalchemy import select

from app.models import Employee, Grp, LitellmWrite
from app.outbox.worker import drain
from app.writes import WriteContext

DANA = "dana@corp.example"


async def _write(factory, settings, steps: list[tuple], *, chain: bool) -> list[int]:
    """Enqueue ``(entity, id, endpoint[, payload])`` steps in one context; return row ids."""
    async with factory() as s:
        ctx = WriteContext(s, settings, "tester@corp.example", str(uuid.uuid4()), "test")
        ids = [
            await ctx.enqueue(e, i, ep, payload=(rest[0] if rest else None), chain=chain)
            for e, i, ep, *rest in steps
        ]
        await s.commit()
    return ids


async def _rows(factory) -> list[LitellmWrite]:
    async with factory() as s:
        return list((await s.scalars(select(LitellmWrite).order_by(LitellmWrite.id))).all())


async def test_dependent_held_while_predecessor_pending(seeded, prov_settings, litellm, fake_app):
    a, b = await _write(
        seeded,
        prov_settings,
        [("department", "1", "/organization/new"), ("department", "2", "/organization/new")],
        chain=True,
    )
    rows = {r.id: r for r in await _rows(seeded)}
    assert (rows[a].depends_on, rows[b].depends_on) == (None, a)
    fake_app.state.fake.outage = True
    res = await drain(seeded, litellm, prov_settings)
    assert res.stopped and res.applied == 0
    rows = {r.id: r for r in await _rows(seeded)}
    assert (rows[a].status, rows[b].status, rows[b].attempts) == ("pending", "pending", 0)
    fake_app.state.fake.outage = False
    res = await drain(seeded, litellm, prov_settings)
    assert (res.applied, res.held) == (2, 0)


async def test_failed_predecessor_holds_chain_until_retry(seeded, prov_settings, litellm):
    # D1's org does not exist yet, so the update fails (404) and holds the chained /new.
    a, b = await _write(
        seeded,
        prov_settings,
        [("department", "1", "/organization/update"), ("department", "2", "/organization/new")],
        chain=True,
    )
    res = await drain(seeded, litellm, prov_settings)
    assert (res.failed, res.applied, res.held, res.stopped) == (1, 0, 1, False)
    rows = {r.id: r for r in await _rows(seeded)}
    assert (rows[a].status, rows[b].status) == ("failed", "pending")
    from app.litellm_client import OrganizationNew

    await litellm.organization_new(OrganizationNew(organization_id="D1", organization_alias="d1"))
    async with seeded() as s, s.begin():
        (await s.get(LitellmWrite, a)).status = "pending"  # what POST /outbox/{id}/retry does
    res = await drain(seeded, litellm, prov_settings)
    assert (res.applied, res.failed, res.held) == (2, 0, 0)
    assert {r.status for r in await _rows(seeded)} == {"applied"}


async def test_frozen_payload_not_rebuilt(provisioned, prov_settings, litellm):
    async with provisioned() as s:
        old = await s.scalar(
            select(Grp).join(Employee, Employee.group_id == Grp.id).where(Employee.upn == DANA)
        )
        other = await s.scalar(select(Grp).where(Grp.id != old.id, Grp.kind == "regular"))
        old_team = old.litellm_team_id
    payload = {"team_id": old_team, "user_id": DANA}
    await _write(
        provisioned,
        prov_settings,
        [("employee", DANA, "/team/member_delete", payload)],
        chain=False,
    )
    async with provisioned() as s, s.begin():  # the desired state moved on; the frozen row must not
        (await s.get(Employee, DANA)).group_id = other.id
    res = await drain(provisioned, litellm, prov_settings)
    assert (res.applied, res.failed) == (1, 0)
    members = {m.user_id for m in (await litellm.team_info(old_team)).team_memberships}
    assert DANA not in members
    assert (await _rows(provisioned))[-1].payload == payload


async def test_delete_404_counts_as_applied(seeded, prov_settings, litellm):
    g = "ghost@corp.example"
    ident = {"jwt_issuer": "https://adfs.test", "jwt_claim_name": "upn", "jwt_claim_value": g}
    steps = [
        ("employee", g, "/team/member_delete", {"team_id": "nope", "user_id": g}),
        ("employee", g, "/key/delete", {"keys": ["no-such-token"]}),
        ("employee", g, "/user/delete", {"user_ids": [g]}),
        ("employee", g, "/jwt/key/mapping/delete", ident),
    ]
    await _write(seeded, prov_settings, steps, chain=True)
    res = await drain(seeded, litellm, prov_settings)
    assert (res.applied, res.failed, res.held) == (4, 0, 0)
    assert {r.status for r in await _rows(seeded)} == {"applied"}


async def test_unchained_rows_keep_phase1_behavior(seeded, prov_settings, litellm):
    a, b = await _write(
        seeded,
        prov_settings,
        [("department", "1", "/organization/update"), ("department", "2", "/organization/new")],
        chain=False,
    )
    rows = {r.id: r for r in await _rows(seeded)}
    assert rows[a].depends_on is None and rows[b].depends_on is None
    res = await drain(seeded, litellm, prov_settings)
    assert (res.failed, res.applied, res.held) == (1, 1, 0)
