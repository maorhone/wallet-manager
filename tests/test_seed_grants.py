"""Step 2.4 seed: the dev grant exists only with SEED_GRANTS (Phase 1 numbers stay otherwise)."""

from sqlalchemy import select

from app.models import Grant, GrantShare
from app.outbox.worker import drain
from app.seed import seed


async def test_seed_grants_only_when_enabled(seeded, prov_settings, litellm):
    await seed(seeded, prov_settings.seed_file, settings=prov_settings)
    async with seeded() as s:
        assert (await s.scalars(select(Grant))).all() == []

    on = prov_settings.model_copy(update={"seed_grants": True})
    for _ in range(2):  # idempotent
        await seed(seeded, on.seed_file, settings=on)
    res = await drain(seeded, litellm, on)
    assert res.failed == 0
    async with seeded() as s:
        (g,) = (await s.scalars(select(Grant))).all()
        (sh,) = (await s.scalars(select(GrantShare))).all()
    assert (g.project_ref, g.amount, g.status, sh.upn) == (
        "PRJ-ATLAS", 30000, "active", "avi@corp.example",
    )  # fmt: skip
