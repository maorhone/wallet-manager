from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.services.attribution import member_spend_by_period


async def test_review_local_month_bounds():
    # Both logs have the same UTC day, on opposite sides of Jerusalem's October boundary.
    logs = [
        (datetime(2026, 9, 30, 20, 59, tzinfo=UTC), D(3)),
        (datetime(2026, 9, 30, 21, 1, tzinfo=UTC), D(7)),
    ]

    class Reader:
        async def spend_rows(self, start, end):
            return [
                SimpleNamespace(day=at.date(), spend=money, team_id="A1", user="member")
                for at, money in logs
                if start <= at < end
            ]

    class Client:
        async def team_info(self, team):
            return SimpleNamespace(team_memberships=[])

    result = await member_spend_by_period(
        None,
        Client(),
        Reader(),
        "member",
        "A1",
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 11, 2, tzinfo=UTC),
        ZoneInfo("Asia/Jerusalem"),
    )
    assert result["2026-09"] == D(3)
    assert result["2026-10"] == D(7)
