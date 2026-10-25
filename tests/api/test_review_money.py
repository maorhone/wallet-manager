"""Phase 5 split review B1 (money core): regression tests, one per finding."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text

from app.models import (
    Employee,
    Grp,
    Initiative,
    LitellmWrite,
    Organization,
    ProductionSystem,
    Request,
    Transfer,
)
from app.outbox.worker import drain
from tests.api.test_requests import send as rsend
from tests.api.test_requests import submit
from tests.api.test_transfers import A1, move, send

DANI, YAEL, DANA = "dani@corp.example", "yael@corp.example", "dana@corp.example"


async def _log(fake_engine, i, team, user, spend, api_key=None):
    now = datetime.now(UTC)
    async with fake_engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id, model_group,'
                ' api_key, "startTime", "endTime", spend)'
                " VALUES (:i, :u, :t, 'gpt-4', :k, :at, :at, :s)"
            ),
            {"i": f"rv{i}", "u": user, "t": team, "k": api_key, "at": now, "s": spend},
        )


async def _initiative(sf, budget=2000, owner=YAEL):
    async with sf() as s:
        row = Initiative(
            name="Pilot", owner_upn=owner, group_id=A1, department_id=1, budget=budget,
            created_by=DANA,
        )  # fmt: skip
        s.add(row)
        await s.commit()
        return row.id


async def _child(sf, base=100, cap=50):
    async with sf() as s:
        g = Grp(
            litellm_team_id="A1-sub", department_id=1, name="A1 sub", base_budget=base,
            default_member_cap=0, status="active", parent_group_id=A1,
        )  # fmt: skip
        s.add(g)
        await s.flush()
        s.add(Employee(upn="kid@corp.example", group_id=g.id, base_cap=cap, status="active"))
        await s.commit()
        return g.id


# review-p5 blocker 1: numeric aliases of one entity must not create credit
async def test_review_alias_same_entity_rejected(login_wm, provisioned):
    iid = await _initiative(provisioned)
    head = await login_wm("dana")
    body = move(10, str(iid), f"0{iid}", ("initiative", "initiative"))
    r = await send(head, "POST", "/transfers", body)
    assert r.status_code in (404, 422), r.text
    async with provisioned() as s:
        assert (await s.get(Initiative, iid)).budget == 2000
        assert (await s.scalars(select(Transfer))).first() is None


# review-p5 blocker 2: employee endpoints follow the personal-value rules
async def test_review_transfer_cannot_raise_own_cap(login_wm, provisioned):
    head = await login_wm("dana")
    r = await send(head, "POST", "/transfers", move(100, DANI, DANA))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "SELF_ALLOCATION_FORBIDDEN"
    r = await send(head, "POST", "/transfers", move(100, DANA, YAEL))
    assert r.status_code == 422, r.text
    async with provisioned() as s:
        assert (await s.get(Employee, DANA)).base_cap == 15000


# review-p5 blocker 3: /team/update for a production system carries the system's own fields
async def test_review_system_update_builds_system_team(provisioned, prov_settings):
    async with provisioned() as s:
        sysrow = ProductionSystem(
            department_id=1, name="Own", budget=700, managing_group_id=A1,
            litellm_team_id="sys-own",
        )  # fmt: skip
        s.add(sysrow)
        await s.commit()
        sid = sysrow.id
        assert await s.get(Grp, sid) is not None  # the id also names a group: the builder must not
        from app.outbox.desired import build

        body = await build(s, prov_settings, "production_system", "/team/update", str(sid))
    assert body.payload()["team_id"] == "sys-own"
    assert body.payload()["max_budget"] == 700


# review-p5 blocker 4: source reduction first; destination write waits for it; failure rolls back
async def test_review_transfer_partial_failure_compensates(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    head = await login_wm("dana")
    fake_app.state.fake.failures["/team/member_update"] = [400, 1]
    r = await send(head, "POST", "/transfers", move(500))
    assert r.status_code == 200, r.text
    tid = int(r.json()["entities"][0]["id"])
    async with provisioned() as s:
        row = await s.get(Transfer, tid)
        source_write, target_write = [await s.get(LitellmWrite, i) for i in row.write_ids]
        assert (source_write.entity_id, target_write.entity_id) == (DANI, YAEL)
        assert target_write.depends_on == source_write.id
    await drain(provisioned, litellm, prov_settings)
    assert (await head.get(f"/transfers/{tid}")).json()["status"] == "failed"
    async with provisioned() as s:
        assert (await s.get(Employee, DANI)).base_cap == 15000
        assert (await s.get(Employee, YAEL)).base_cap == 15000
    team = await litellm.team_info("A1")
    assert team is not None


async def test_review_initiative_credit_gated_until_source_applied(
    login_wm, provisioned, litellm, prov_settings
):
    iid = await _initiative(provisioned, budget=1000)
    head = await login_wm("dana")
    r = await send(
        head, "POST", "/transfers", move(500, DANI, str(iid), ("employee", "initiative"))
    )
    assert r.status_code == 200, r.text
    key = {
        "owner_kind": "initiative",
        "owner_id": iid,
        "max_budget": 1400,
        "alias": "k",
        "max_parallel": 1,
        "rpm_limit": 10,
    }
    denied = await send(head, "POST", "/service-keys", key)
    assert denied.status_code == 422, denied.text
    assert denied.json()["code"] == "PARENT_OVERALLOCATED"
    await drain(provisioned, litellm, prov_settings)
    allowed = await send(head, "POST", "/service-keys", key)
    assert allowed.status_code == 200, allowed.text


# review-p5 blocker 5: fresh spend, never the cache; unreadable spend refuses
async def test_review_transfer_uses_fresh_spend(login_wm, provisioned, fake_engine):
    await _log(fake_engine, 1, "A1", DANI, 14500)  # no snapshot row exists
    head = await login_wm("dana")
    r = await send(head, "POST", "/transfers", move(1000))
    assert r.status_code == 422, r.text
    assert r.json()["details"]["reason"] == "exceeds_unused"


async def test_review_transfer_refused_without_spend_source(login, provisioned):
    head = await login(
        "dana", litellm_database_url=None
    )  # this app has no LiteLLM read-only reader
    r = await send(head, "POST", "/transfers", move(10))
    assert r.status_code == 503, r.text


# review-p5 blocker 6: group commitments = consumption + unspent allocations
async def test_review_group_commitment_counts_spent_plus_unspent(
    login_wm, provisioned, fake_engine
):
    child = await _child(provisioned, base=100, cap=50)
    iid = await _initiative(provisioned)
    await _log(fake_engine, 2, "A1-sub", "ghost@corp.example", 50)
    head = await login_wm("dana")
    r = await send(
        head, "POST", "/transfers", move(50, str(child), str(iid), ("group", "initiative"))
    )
    assert r.status_code == 422, r.text
    assert r.json()["details"]["reason"] == "exceeds_unused"


# review-p5 blocker 7: org-system reductions keep consumed credit
async def test_review_org_system_floor_keeps_consumption(login_wm, provisioned, snapshot):
    async with provisioned() as s:
        (await s.get(Organization, 1)).systems_envelope = Decimal(1000)
        row = ProductionSystem(
            funding="organization", organization_id=1, name="Org", budget=100,
            litellm_team_id="sys-org",
        )  # fmt: skip
        s.add(row)
        await s.commit()
        sid = row.id
    await snapshot([("system", sid, None, 80)])
    admin = await login_wm("admin")
    r = await send(admin, "PUT", f"/organizations/1/systems/{sid}/budget", {"budget": 0})
    assert r.status_code == 422, r.text
    r = await send(admin, "PUT", f"/organizations/1/systems/{sid}/budget", {"budget": 80})
    assert r.status_code == 200, r.text


# review-p5 major: D-045 narrative-only throughput request; AI team fills the numbers
async def test_review_throughput_narrative_only(login_wm, provisioned, litellm, prov_settings):
    dani, dana, rep = await login_wm("dani"), await login_wm("dana"), await login_wm("rep")
    ai = await login_wm("aiteam")
    rid = await submit(dani, "throughput", payload={"need": "ten agents in parallel"})
    for who in (dana, rep):
        assert (
            await rsend(who, "POST", f"/requests/{rid}/decide", {"decision": "forward"})
        ).is_success
    empty = await rsend(ai, "POST", f"/requests/{rid}/decide", {"decision": "apply"})
    assert empty.status_code == 422, empty.text
    done = await rsend(
        ai, "POST", f"/requests/{rid}/decide", {"decision": "apply", "max_parallel": 6}
    )
    assert done.status_code == 200, done.text
    async with provisioned() as s:
        assert (await s.get(Employee, DANI)).max_parallel == 6


# review-p5 major: D-046..D-048 a request may target an initiative the requester owns
async def test_review_request_targets_owned_initiative(login_wm, provisioned):
    iid = await _initiative(provisioned, budget=1000, owner=YAEL)
    yael, dana, dani = await login_wm("yael"), await login_wm("dana"), await login_wm("dani")
    target = {"kind": "initiative", "id": iid}
    foreign = await rsend(
        dani, "POST", "/requests",
        {"type": "credits", "amount": 100, "payload": {"need": "x"}, "target": target},
    )  # fmt: skip
    assert foreign.status_code in (403, 404, 422), foreign.text
    rid = await submit(yael, "credits", amount=300, target=target)
    got = await rsend(dana, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    assert got.status_code == 200, got.text
    async with provisioned() as s:
        assert (await s.get(Initiative, iid)).budget == 1300
        assert (await s.get(Employee, YAEL)).base_cap == 15000
        assert (await s.get(Request, rid)).status == "applied"


# review-p5 major: over-allocation counts internal systems and initiatives
async def test_review_overallocation_counts_new_entities(login_wm, provisioned):
    async with provisioned() as s:
        s.add(ProductionSystem(department_id=1, name="Own", budget=1, litellm_team_id="sys-x"))
        s.add(
            Initiative(
                name="Big", owner_upn=YAEL, group_id=A1, department_id=1, budget=10**6,
                created_by=DANA,
            )
        )  # fmt: skip
        await s.commit()
    r = await (await login_wm("admin")).get("/reports/overallocation")
    got = {(f["level"], f["entity_id"]) for f in r.json()["findings"] if f["source"] == "wm"}
    assert ("department", 1) in got
    assert ("group", A1) in got
