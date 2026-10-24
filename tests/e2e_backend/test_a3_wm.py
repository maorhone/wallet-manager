import httpx


async def test_a3_limits_set_via_wm_enforced(login_wm, drain_all, employee, fake_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as http:
        await http.post("/_test/config", json={"latency_ms": 500})
    dana = await login_wm("aiteam")  # D-065: only the AI team sets limits
    h = {"X-CSRF-Token": dana.cookies["wm_csrf"], "X-Change-Reason": "limit"}
    r = await dana.put(
        "/groups/1/members/dani@corp.example/limits", json={"max_parallel": 3}, headers=h
    )
    assert r.status_code == 200, r.text
    await drain_all()

    resps = await employee("dani@corp.example").chat_n(6)
    assert sorted(r.status_code for r in resps) == [200, 200, 200, 429, 429, 429]
