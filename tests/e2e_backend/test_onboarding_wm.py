from sqlalchemy import update

from app.models import Grp

NEWBIE, DRIFTER = "newbie@corp.example", "drifter@corp.example"


async def test_a15_onboarded_user_jwt_request_succeeds(portal, provisioned, employee, drain_all):
    async with provisioned() as s:  # B1's default cap; the seed leaves it at 0
        await s.execute(update(Grp).where(Grp.id == 3).values(default_member_cap=5000))
        await s.commit()
    newbie = employee(NEWBIE)
    assert (await newbie.chat()).status_code in (401, 403)  # unknown to LiteLLM

    assert (await portal.post(NEWBIE)).status_code == 201
    assert (await newbie.chat()).status_code in (401, 403)  # joiner rows not applied yet
    await drain_all()
    assert (await portal.get(NEWBIE)).json()["status"] == "ready"
    assert (await newbie.chat()).status_code == 200


async def test_a10_pending_user_rejected(portal, provisioned, employee, drain_all):
    assert (await portal.post(DRIFTER)).json()["status"] == "pending_placement"
    await drain_all()
    assert (await employee(DRIFTER).chat()).status_code in (401, 403)
