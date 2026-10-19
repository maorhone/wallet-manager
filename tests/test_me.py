async def test_me_lists_scopes_for_two_roles(login):
    client = await login("noa")
    body = (await client.get("/me")).json()
    assert body["upn"] == "noa@corp.example"
    assert body["display_name"] == "Noa Katz"
    assert body["group"]["name"] == "Group A2" and body["department"]["name"] == "Department D1"
    assert {(r["role"], r["scope_kind"], r["scope_name"]) for r in body["roles"]} == {
        ("employee", "self", None),
        ("group_head", "group", "Group A2"),
        ("viewer", "department", "Department D1"),
    }
    assert body["scopes"][0] == {"kind": "self", "id": None, "name": None, "role": None}
    assert {(s["kind"], s["role"]) for s in body["scopes"]} == {
        ("self", None),
        ("group", "group_head"),
        ("department", "viewer"),
    }
    assert body["timezone"] == "Asia/Jerusalem"
    assert len(body["current_period"]) == 7 and body["dev_auth"] is True


async def test_me_platform_scope_for_admin(login):
    body = (await (await login("admin")).get("/me")).json()
    assert {"kind": "platform", "id": None, "name": None, "role": "platform_admin"} in body[
        "scopes"
    ]


async def test_me_401_without_session(make_client):
    r = await (await make_client()).get("/me")
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
