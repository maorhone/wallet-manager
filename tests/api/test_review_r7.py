"""Phase 5 round-6 re-review (fixed in round 7): regression tests."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.models import Department
from tests.api.test_transfers import move, send

DANI = "dani@corp.example"


async def _log_at(fake_engine, i, team, user, spend, at):
    async with fake_engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id, model_group,'
                ' api_key, "startTime", "endTime", spend)'
                " VALUES (:i, :u, :t, 'gpt-4', NULL, :at, :at, :s)"
            ),
            {"i": f"r7-{i}", "u": user, "t": team, "at": at, "s": spend},
        )


# review-p5-r6 blocker B1a2: multi-month budgets include last month's consumption
async def test_r7_transfer_reads_active_multi_month_window(login_wm, provisioned, fake_engine):
    last_month = datetime.now(UTC).replace(day=1, hour=12) - timedelta(days=3)
    await _log_at(fake_engine, 1, "A1", DANI, 14500, last_month)
    async with provisioned() as s:
        (await s.get(Department, 1)).budget_period = "2mo"
        await s.commit()
    head = await login_wm("dana")
    r = await send(head, "POST", "/transfers", move(1000))
    assert r.status_code == 422, r.text
    assert r.json()["details"]["reason"] == "exceeds_unused"


# review-p5-r6 blocker B1c: approval re-reads the group budget under the allocation locks
async def test_r7_approval_refreshes_group_after_locks(login_wm, provisioned, monkeypatch):
    from app.models import Grp
    from app.writes import WriteContext
    from tests.api.test_requests import submit

    async with provisioned() as s:
        g = await s.get(Grp, 1)
        g.overalloc_factor = 1
        base = g.base_budget
        await s.commit()
    rid = await submit(await login_wm("dani"), amount=1000)
    real_lock = WriteContext.lock

    async def lock(self, kind, id):
        await real_lock(self, kind, id)
        if kind.name == "GROUP":  # a concurrent reduction commits while approval waited
            async with provisioned() as other:
                (await other.get(Grp, 1)).base_budget = 1
                await other.commit()

    monkeypatch.setattr(WriteContext, "lock", lock)
    r = await send(
        await login_wm("dana"), "POST", f"/requests/{rid}/decide",
        {"decision": "approve", "amount": 1000},
    )  # fmt: skip
    assert base > 1
    assert r.status_code == 422, r.text


# review-p5-r6 blocker B1a1: an aggregate destination write settles every receipt it published
async def test_r7_aggregate_destination_write_settles_siblings(
    login_wm, provisioned, litellm, prov_settings
):
    from app.models import Transfer
    from app.outbox.worker import drain

    head = await login_wm("dana")
    a = await send(head, "POST", "/transfers", move(10))
    b = await send(head, "POST", "/transfers", move(20))
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        states = [t.state for t in await s.scalars(select(Transfer).order_by(Transfer.id))]
    assert states == ["applied", "applied"]


# review-p5-r6 major B1a1: reciprocal pending transfers would wait on each other forever
async def test_r7_reciprocal_transfer_refused(login_wm, provisioned):
    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(20))).status_code == 200
    r = await send(head, "POST", "/transfers", move(20, "yael@corp.example", "dani@corp.example"))
    assert r.status_code == 422, r.text
    assert r.json()["details"]["reason"] == "dependency_cycle"


# review-p5-r6 major B1c: a targeted throughput decision needs and records the limits
async def test_r7_targeted_throughput_requires_and_records_limits(login_wm, provisioned):
    from app.models import Initiative, Request
    from tests.api.test_requests import submit

    async with provisioned() as s:
        row = Initiative(
            name="P", owner_upn="yael@corp.example", group_id=1, department_id=1, budget=10,
            created_by="dana@corp.example",
        )  # fmt: skip
        s.add(row)
        await s.commit()
        iid = row.id
    rid = await submit(
        await login_wm("yael"), "throughput", target={"kind": "initiative", "id": iid}
    )
    for who in ("dana", "rep"):
        r = await send(
            await login_wm(who), "POST", f"/requests/{rid}/decide", {"decision": "forward"}
        )
        assert r.status_code == 200, r.text
    ai = await login_wm("aiteam")
    path = f"/requests/{rid}/decide"
    empty = await send(ai, "POST", path, {"decision": "apply"})
    assert empty.status_code == 422, empty.text
    done = await send(ai, "POST", path, {"decision": "apply", "max_parallel": 7})
    assert done.status_code == 200, done.text
    async with provisioned() as s:
        req = await s.get(Request, rid)
        assert (req.status, req.payload["applied_limits"]) == ("applied", {"max_parallel": 7})


async def _owned(provisioned):
    from app.models import Initiative, ProductionSystem, ServiceKey

    async with provisioned() as s:
        system = ProductionSystem(
            department_id=1, name="Own", budget=10, litellm_team_id="sys-r7",
            owner_upn="yael@corp.example",
        )  # fmt: skip
        init = Initiative(
            name="P", owner_upn="yael@corp.example", group_id=1, department_id=1, budget=10,
            created_by="dana@corp.example",
        )  # fmt: skip
        s.add_all([system, init])
        await s.flush()
        s.add_all(
            [
                ServiceKey(owner_kind="system", owner_id=system.id, max_budget=1, status="active"),
                ServiceKey(owner_kind="initiative", owner_id=init.id, max_budget=1, status="active"),
            ]
        )
        await s.commit()
        return system.id, init.id


# review-p5-r6 major B2: an internal-system owner reads its keys (never writes)
async def test_r7_internal_system_owner_reads_keys(login_wm, provisioned):
    sid, _ = await _owned(provisioned)
    yael = await login_wm("yael")
    r = await yael.get(f"/service-keys?owner_kind=system&owner_id={sid}")
    assert r.status_code == 200, r.text
    assert [i["actions"] for i in r.json()["items"]] == [{"rotate": False, "revoke": False}]
    other = await (await login_wm("dani")).get(f"/service-keys?owner_kind=system&owner_id={sid}")
    assert other.status_code == 403


# review-p5-r6 major B2: advertised actions follow the mutation authorization
async def test_r7_actions_for_caller_with_rep_and_head(provisioned):
    from app.permissions import Binding, Principal
    from app.services.service_keys import list_keys

    _, iid = await _owned(provisioned)
    caller = Principal(
        "multi@corp.example", "Multi",
        [Binding("department_rep", "department", 1), Binding("group_head", "group", 1)],
    )  # fmt: skip

    class Clock:
        def now(self):
            return datetime.now(UTC)

    from app.config import Settings

    async with provisioned() as s:
        got = await list_keys(s, caller, Settings(_env_file=None), Clock(), "initiative", iid)
    assert got["items"][0]["actions"] == {"rotate": True, "revoke": True}
