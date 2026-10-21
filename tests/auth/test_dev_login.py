async def test_dev_login_404_without_DEV_AUTH(make_client):
    client = await make_client(dev_auth=False)
    assert (await client.get("/auth/dev/users")).status_code == 404
    r = await client.post("/auth/dev/login", json={"upn": "dana@corp.example"})
    assert r.status_code == 404


async def test_dev_login_sets_session(make_client):
    client = await make_client(dev_auth=True)
    users = (await client.get("/auth/dev/users")).json()
    noa = next(u for u in users if u["upn"] == "noa@corp.example")
    assert noa["roles"] == ["group_head", "viewer"]

    r = await client.post("/auth/dev/login", json={"upn": "dana@corp.example"})
    assert r.status_code == 200
    assert "wm_session" in client.cookies and "wm_csrf" in client.cookies
    set_cookie = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith("wm_session"))
    assert "httponly" in session_cookie.lower() and "samesite=lax" in session_cookie.lower()
    assert (await client.get("/me")).json()["upn"] == "dana@corp.example"

    assert (await client.post("/auth/dev/login", json={"upn": "x@corp.example"})).status_code == 404


async def test_dev_login_works_when_app_directory_is_ldap(make_client):
    """PLAN §3.1: dev auth keeps its own fixture directory even when DIRECTORY_BACKEND=ldap."""
    client = await make_client(
        dev_auth=True,
        directory_backend="ldap",
        ldap_url="ldaps://dc.corp.example",
        ldap_user_base="OU=Users,DC=corp,DC=example",
    )
    users = (await client.get("/auth/dev/users")).json()
    assert any(u["upn"] == "dana@corp.example" for u in users)

    r = await client.post("/auth/dev/login", json={"upn": "dana@corp.example"})
    assert r.status_code == 200, r.text
