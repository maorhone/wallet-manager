MODEL = "test-model"
DANI, YAEL = "dani@corp.example", "yael@corp.example"


async def _put(client, path, body):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], "X-Change-Reason": "pot"}
    return await client.put(path, json=body, headers=h)


async def test_a5_pots_and_caps_via_wm(login_wm, drain_all, employee):
    admin, rep, dana = await login_wm("admin"), await login_wm("rep"), await login_wm("dana")
    assert (await _put(admin, "/departments/1/model-allowances", {MODEL: 100})).status_code == 200
    assert (await _put(rep, "/groups/1/model-pots", {MODEL: 10})).status_code == 200
    for upn in (DANI, YAEL):
        r = await _put(dana, f"/groups/1/members/{upn}/model-caps", {MODEL: 4})
        assert r.status_code == 200, r.text
    await drain_all()

    for upn in (DANI, YAEL):
        who = employee(upn)
        assert all(r.status_code == 200 for r in await who.chat_n(4))
        blocked = await who.chat()
        assert blocked.status_code == 400
        assert blocked.json()["error"]["scope"] == "member_model"

    r = await _put(dana, f"/groups/1/members/{DANI}/model-caps", {MODEL: 7})  # 7 + 4 > pot 10
    assert r.status_code == 422
    body = r.json()
    assert (body["code"], body["requested"], body["available"]) == ("PARENT_OVERALLOCATED", 11, 10)
