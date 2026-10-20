DANI = "dani@corp.example"


async def _post(client, path):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], "X-Change-Reason": "test"}
    r = await client.post(path, json={}, headers=h)
    assert r.status_code == 200, r.text


async def test_a14_kick_rejects_jwt_restore_accepts(login_wm, drain_all, employee):
    dana, dani = await login_wm("dana"), employee(DANI)
    assert (await dani.chat()).status_code == 200
    await _post(dana, f"/groups/1/members/{DANI}/kick")
    await drain_all()
    assert (await dani.chat()).status_code in (401, 403)
    await _post(dana, f"/groups/1/members/{DANI}/restore")
    await drain_all()
    assert (await dani.chat()).status_code == 200
