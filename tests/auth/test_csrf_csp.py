async def test_write_without_csrf_403(login):
    client = await login("dana")
    r = await client.put("/groups/1/members", json=[])
    assert r.status_code == 403 and r.json()["code"] == "CSRF_FAILED"

    r = await client.put("/groups/1/members", json=[], headers={"X-CSRF-Token": "wrong"})
    assert r.json()["code"] == "CSRF_FAILED"

    token = client.cookies["wm_csrf"]
    r = await client.put("/groups/1/members", json=[], headers={"X-CSRF-Token": token})
    assert r.json().get("code") != "CSRF_FAILED"  # passes the middleware (route comes later)

    assert (await client.get("/me")).status_code == 200  # reads need no token
    assert (await client.post("/auth/logout")).status_code == 204  # /auth/* exempt


async def test_csp_header_present(make_client):
    client = await make_client()
    for path in ("/healthz", "/me"):  # also on error responses
        r = await client.get(path)
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "script-src 'self'" in csp
        assert r.headers["x-content-type-options"] == "nosniff"
