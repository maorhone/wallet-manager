import pytest


@pytest.fixture
def put():
    """PUT json with CSRF and a reason."""

    async def _put(client, path, body, reason="plan", **headers):
        h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
        if reason is not None:
            h["X-Change-Reason"] = reason
        return await client.put(path, json=body, headers=h)

    return _put


@pytest.fixture
def split(put):
    """PUT /departments/1/split; D1 is exactly full (bases 770000 + pmo 180000 + reserve 50000)."""

    async def _split(client, groups, pmo_pool=180000, reserve=50000, dept_id=1, **headers):
        body = {"groups": groups, "pmo_pool": pmo_pool, "reserve": reserve}
        return await put(client, f"/departments/{dept_id}/split", body, "split", **headers)

    return _split
