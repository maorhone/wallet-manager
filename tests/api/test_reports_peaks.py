import httpx
import pytest

from app.models import Employee, RoleBinding


async def peaks(client, **params):
    return await client.get(
        "/reports/concurrency-peaks",
        params={
            "scope": "global",
            "from": "2026-06-01",
            "to": "2026-06-02",
            **params,
        },
    )


@pytest.fixture
async def peak_logs(provisioned, fake_app):
    rows = [
        dict(
            team_id=t,
            user_id=u + "@corp.example",
            model="m",
            amount=0,
            start=s,
            end=e,
            api_key="dummy-report-key",
        )
        for t, u, s, e in [
            ("A1", "dani", "2026-06-01T21:30:00Z", "2026-06-01T22:30:00Z"),
            ("A1", "dani", "2026-06-01T21:45:00Z", "2026-06-01T22:00:00Z"),
            ("C1", "viewer", "2026-06-01T21:45:00Z", "2026-06-01T22:00:00Z"),
        ]
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        r = await c.post("/_test/spend/bulk", json=rows)
        assert r.status_code == 200, r.text


async def test_peaks_per_employee_and_global_from_spendlogs(login_wm, peak_logs):
    r = await peaks(await login_wm("admin"))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["daily"] == [{"day": "2026-06-02", "peak": 3, "at": "2026-06-01T21:45:00Z"}]
    assert [(m["upn"], m["peak"]) for m in b["members"]] == [
        ("dani@corp.example", 2),
        ("viewer@corp.example", 1),
    ]
    assert b["members"][0]["display_name"] == "Dani Cohen"
    assert "max_parallel" in b["members"][0]
    own = (await peaks(await login_wm("dani"), scope="employee", id="dani@corp.example")).json()
    assert own["daily"][0]["peak"] == 2 and len(own["members"]) == 1
    viewer_client = await login_wm("viewer")
    assert (await peaks(viewer_client)).status_code == 403
    viewer = (await peaks(viewer_client, scope="department", id=1)).json()
    assert viewer["daily"][0]["peak"] == 2 and viewer["members"] == []


async def test_department_scope_filters_teams(login_wm, peak_logs):
    b = (await peaks(await login_wm("rep"), scope="department", id=1)).json()
    assert b["daily"][0]["peak"] == 2
    assert [m["upn"] for m in b["members"]] == ["dani@corp.example"]
    assert (await peaks(await login_wm("rep"), scope="department", id=2)).status_code == 404
    assert (await peaks(await login_wm("pmo"), scope="department", id=1)).status_code == 403


async def test_range_over_31_days_422(login):
    r = await peaks(await login("admin"), to="2026-07-02")
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"


async def test_global_viewer_and_top_100(login_wm, provisioned, fake_app):
    async with provisioned() as db:
        db.add(
            RoleBinding(
                ad_group_dn="CN=wm-viewer-d1,OU=Groups,DC=corp,DC=example",
                role="viewer",
                scope_kind="global",
            )
        )
        db.add_all(
            Employee(upn=f"peak-{i:03}@corp.example", group_id=1, base_cap=1, max_parallel=4)
            for i in range(101)
        )
        await db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        r = await c.post(
            "/_test/spend/bulk",
            json=[
                dict(
                    team_id="A1",
                    user_id=f"peak-{i:03}@corp.example",
                    model="m",
                    amount=0,
                    start="2026-06-01T10:00:00Z",
                    end="2026-06-01T11:00:00Z",
                )
                for i in range(101)
            ],
        )
        assert r.status_code == 200, r.text
    b = (await peaks(await login_wm("admin"))).json()
    assert b["daily"][0]["peak"] == 101 and len(b["members"]) == 100
    assert [m["upn"] for m in b["members"]] == [f"peak-{i:03}@corp.example" for i in range(100)]
    assert all(m["max_parallel"] == 4 for m in b["members"])
    viewer = (await peaks(await login_wm("viewer"))).json()
    assert viewer["daily"] == b["daily"] and viewer["members"] == []
