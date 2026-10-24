from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass
class FakeState:
    engine: AsyncEngine
    master_key: str
    outage: bool = False
    latency_ms: int = 0
    cost_per_request: float = 1
    in_flight: dict[str, int] = field(default_factory=dict)  # R7 per-token counter
    failures: dict[str, list[int]] = field(default_factory=dict)  # path -> [status, times left]
    clock_offset_s: float = 0  # test clock: added to the wall clock by ``now()``
    timezone: str = "Asia/Jerusalem"  # budget periods follow APP_TIMEZONE
    redis: str = "none"  # none | ok | down (§5.4 Redis status, reported by /health/readiness)

    model_health: dict[str, str | None] = field(default_factory=dict)  # model -> error or None

    def now(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self.clock_offset_s)

    def reset(self) -> None:
        self.clock_offset_s = 0
        self.failures.clear()
        self.outage = False
        self.latency_ms = 0
        self.cost_per_request = 1
        self.in_flight.clear()
        self.redis = "none"
        self.model_health.clear()
