"""Read-only SQL against the LiteLLM database (R19, §11.3). Use the ``litellm_ro`` role."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# LiteLLM stores spend/budgets as double precision; go through str to avoid float noise.
_ZERO = Decimal(0)


def _dec(value: object) -> Decimal:
    return _ZERO if value is None else Decimal(str(value))


# §11.3 query 1, plus organization_id, without HAVING (the caller applies dept_budget - reserve).
_ORG_ALLOCATIONS = """
SELECT o.organization_id, o.organization_alias, b.max_budget AS dept_budget,
       SUM(t.max_budget) AS allocated
FROM "LiteLLM_OrganizationTable" o
JOIN "LiteLLM_BudgetTable" b ON b.budget_id = o.budget_id
JOIN "LiteLLM_TeamTable"   t ON t.organization_id = o.organization_id
GROUP BY o.organization_id, o.organization_alias, b.max_budget
ORDER BY o.organization_id
"""

# §11.3 query 2, plus team_id.
_OVERALLOCATED_TEAMS = """
SELECT t.team_id, t.team_alias, t.max_budget AS group_budget,
       SUM(mb.max_budget + CASE WHEN mb.temp_budget_expiry > now()
                                THEN COALESCE(mb.temp_budget_increase, 0) ELSE 0 END) AS allocated
FROM "LiteLLM_TeamTable"      t
JOIN "LiteLLM_TeamMembership" m  ON m.team_id = t.team_id
JOIN "LiteLLM_BudgetTable"    mb ON mb.budget_id = m.budget_id
GROUP BY t.team_id, t.team_alias, t.max_budget
HAVING SUM(mb.max_budget + CASE WHEN mb.temp_budget_expiry > now()
                                THEN COALESCE(mb.temp_budget_increase, 0) ELSE 0 END) > t.max_budget
ORDER BY t.team_id
"""

_SPEND_ROWS = """
SELECT team_id, "user", api_key, model_group,
       ("startTime" AT TIME ZONE :timezone)::date AS day, SUM(spend) AS spend,
       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
       COALESCE(SUM(completion_tokens), 0) AS completion_tokens
FROM "LiteLLM_SpendLogs"
WHERE "startTime" >= :start AND "startTime" < :end
GROUP BY team_id, "user", api_key, model_group, day
ORDER BY day, team_id, "user", model_group
"""

# Tags are LiteLLM request_tags (jsonb array of strings); the project is the first "project:<name>".
_SPEND_BY_TAG = """
SELECT (SELECT substr(t, 9) FROM jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(request_tags) = 'array' THEN request_tags ELSE '[]'::jsonb END
        ) t WHERE t LIKE 'project:_%' LIMIT 1) AS project,
       ("startTime" AT TIME ZONE :timezone)::date AS day, SUM(spend) AS spend
FROM "LiteLLM_SpendLogs"
WHERE "startTime" >= :start AND "startTime" < :end
  AND (CAST(:teams AS text[]) IS NULL OR team_id = ANY(CAST(:teams AS text[])))
  AND (CAST(:users AS text[]) IS NULL OR "user" = ANY(CAST(:users AS text[])))
GROUP BY project, day
ORDER BY day, project NULLS LAST
"""

_FAILED_REQUESTS = """
SELECT "user", team_id, model_group, "startTime", status_code, exception_type,
       exception_string, retry_after
FROM "LiteLLM_ErrorLogs"
WHERE "startTime" >= :start AND "startTime" < :end
  AND (CAST(:teams AS text[]) IS NULL OR team_id = ANY(CAST(:teams AS text[])))
  AND (CAST(:users AS text[]) IS NULL OR "user" = ANY(CAST(:users AS text[])))
ORDER BY "startTime" DESC
LIMIT :limit
"""

# Clip overlaps to local calendar days, then sweep end events before start events.
# Group equal events before the window sum so ties cannot depend on query row order.
_CONCURRENCY_PEAKS = """
WITH days AS (
    SELECT d::date AS day, d AT TIME ZONE :timezone AS lo,
           (d + interval '1 day') AT TIME ZONE :timezone AS hi
    FROM generate_series(
        (CAST(:start AS timestamptz) AT TIME ZONE :timezone)::date::timestamp,
        ((CAST(:end AS timestamptz) - interval '1 microsecond')
            AT TIME ZONE :timezone)::date::timestamp, interval '1 day'
    ) d
), logs AS (
    SELECT "user", "startTime" AS s, "endTime" AS e
    FROM "LiteLLM_SpendLogs"
    WHERE "startTime" < :end AND "endTime" > :start AND "endTime" > "startTime"
      AND (CAST(:teams AS text[]) IS NULL OR team_id = ANY(CAST(:teams AS text[])))
      AND (CAST(:users AS text[]) IS NULL OR "user" = ANY(CAST(:users AS text[])))
), segments AS (
    SELECT "user", day, greatest(s, lo, :start) AS s, least(e, hi, :end) AS e
    FROM logs JOIN days ON s < hi AND e > lo
), events AS (
    SELECT "user", day, s AS t, 1 AS delta FROM segments
    UNION ALL
    SELECT "user", day, e AS t, -1 AS delta FROM segments
), grouped AS (
    SELECT CASE WHEN g = 1 THEN NULL ELSE "user" END AS "user",
           day, t, delta, count(*) * delta AS change
    FROM events CROSS JOIN generate_series(0, 1) g
    WHERE g = 1 OR "user" IS NOT NULL
    GROUP BY CASE WHEN g = 1 THEN NULL ELSE "user" END, day, t, delta
), running AS (
    SELECT "user", day, t,
           sum(change) OVER (PARTITION BY "user", day ORDER BY t, delta
                             ROWS UNBOUNDED PRECEDING) AS peak
    FROM grouped
), ranked AS (
    SELECT *, row_number() OVER (PARTITION BY "user", day ORDER BY peak DESC, t) AS rn
    FROM running
)
SELECT "user", day, peak, t FROM ranked WHERE rn = 1 AND peak > 0
ORDER BY day, "user" NULLS FIRST
"""


@dataclass(frozen=True)
class OrgAllocation:
    organization_id: str
    organization_alias: str | None
    dept_budget: Decimal
    allocated: Decimal


@dataclass(frozen=True)
class OverallocatedTeam:
    team_id: str
    team_alias: str | None
    group_budget: Decimal
    allocated: Decimal


class SpendRow(NamedTuple):
    team_id: str | None
    user: str | None
    api_key: str | None
    model_group: str | None
    day: date
    spend: Decimal
    prompt_tokens: int = 0
    completion_tokens: int = 0


class FailedRow(NamedTuple):
    user: str | None
    team_id: str | None
    model: str | None
    at: datetime
    status_code: int | None
    exception_type: str
    message: str
    retry_after: int | None


class PeakRow(NamedTuple):
    user: str | None
    day: date
    peak: int
    at: datetime


class LiteLLMReader:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def _rows(self, sql: str, **params):
        async with self._engine.connect() as conn:
            return (await conn.execute(text(sql), params)).all()

    async def org_allocations(self) -> list[OrgAllocation]:
        return [
            OrgAllocation(r[0], r[1], _dec(r[2]), _dec(r[3]))
            for r in await self._rows(_ORG_ALLOCATIONS)
        ]

    async def overallocated_teams(self) -> list[OverallocatedTeam]:
        return [
            OverallocatedTeam(r[0], r[1], _dec(r[2]), _dec(r[3]))
            for r in await self._rows(_OVERALLOCATED_TEAMS)
        ]

    async def spend_rows(
        self, start: datetime, end: datetime, *, timezone: str = "UTC"
    ) -> list[SpendRow]:
        """Daily spend per (team, user, key, model group) in ``timezone`` (UTC by default)."""
        return [
            SpendRow(r[0], r[1], r[2], r[3], r[4], _dec(r[5]), int(r[6]), int(r[7]))
            for r in await self._rows(_SPEND_ROWS, start=start, end=end, timezone=timezone)
        ]

    async def spend_by_tag(
        self, start: datetime, end: datetime, *, team_ids=None, users=None, timezone="UTC"
    ) -> list[tuple[str | None, date, Decimal]]:
        """Daily spend per project tag; project None = untagged."""
        return [
            (r[0], r[1], _dec(r[2]))
            for r in await self._rows(
                _SPEND_BY_TAG, start=start, end=end, teams=team_ids, users=users, timezone=timezone
            )
        ]

    async def failed_requests(
        self, start: datetime, end: datetime, *, team_ids=None, users=None, limit: int = 5000
    ) -> list[FailedRow]:
        """Failed requests (LiteLLM_ErrorLogs), newest first; never reads request content."""
        return [
            FailedRow(
                r[0],
                r[1],
                r[2],
                r[3],
                int(r[4]) if r[4] and str(r[4]).isdigit() else None,
                r[5] or "",
                r[6] or "",
                r[7],
            )
            for r in await self._rows(
                _FAILED_REQUESTS, start=start, end=end, teams=team_ids, users=users, limit=limit
            )
        ]

    async def concurrency_peaks(
        self, start: datetime, end: datetime, *, team_ids=None, users=None, timezone="UTC"
    ) -> list[PeakRow]:
        """Daily overlaps in [start, end), including requests already running at start."""
        tz = ZoneInfo(timezone)
        if (
            end <= start
            or (
                (end - timedelta(microseconds=1)).astimezone(tz).date()
                - start.astimezone(tz).date()
            ).days
            >= 31
        ):
            raise ValueError("concurrency window must be positive and at most 31 days")
        return [
            PeakRow(r[0], r[1], int(r[2]), r[3])
            for r in await self._rows(
                _CONCURRENCY_PEAKS,
                start=start,
                end=end,
                teams=team_ids,
                users=users,
                timezone=timezone,
            )
        ]

    async def ping(self) -> bool:
        await self._rows("SELECT 1")
        return True
