from sqlalchemy import select

from app.models import EmployeeModelCap, GroupModelPot
from app.outbox.worker import drain

A1 = 1
DANI, YAEL = "dani@corp.example", "yael@corp.example"


async def _pot(provisioned, model="gpt-4o", amount=1000):
    async with provisioned() as s:
        s.add(GroupModelPot(group_id=A1, model=model, amount=amount))
        await s.commit()


def path(upn):
    return f"/groups/{A1}/members/{upn}/model-caps"


async def test_caps_vs_pot_422(login, put, provisioned, litellm, prov_settings):
    await _pot(provisioned)
    dana = await login("dana")
    r = await put(dana, path(DANI), {"gpt-4o": 600})
    assert r.status_code == 200, r.text
    assert r.json()["entities"][0]["sync_status"] == "pending"
    r = await put(dana, path(YAEL), {"gpt-4o": 600})
    assert r.status_code == 422
    body = r.json()
    assert (body["code"], body["requested"], body["available"]) == (
        "PARENT_OVERALLOCATED",
        1200,
        1000,
    )
    assert body["details"] == {"model": "gpt-4o"}
    # replacing the target's own cap does not count twice
    assert (await put(dana, path(DANI), {"gpt-4o": 1000})).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        rows = (await s.scalars(select(EmployeeModelCap))).all()
        assert {(r.upn, r.model, r.amount) for r in rows} == {(DANI, "gpt-4o", 1000)}


async def test_no_pot_means_zero(login, put, provisioned):
    r = await put(await login("dana"), path(DANI), {"haiku": 1})
    assert r.status_code == 422
    assert (r.json()["requested"], r.json()["available"]) == (1, 0)


async def test_model_caps_dr9(login, put, provisioned):
    await _pot(provisioned)
    r = await put(await login("dana"), path("dana@corp.example"), {"gpt-4o": 1})
    assert r.status_code == 422 and r.json()["code"] == "SELF_ALLOCATION_FORBIDDEN"
    r = await put(await login("rep"), path(DANI), {"gpt-4o": 1})  # rep sets heads, not members
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"
    r = await put(await login("rep"), path("dana@corp.example"), {"gpt-4o": 1})
    assert r.status_code == 200
