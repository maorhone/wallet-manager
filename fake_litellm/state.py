from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass
class FakeState:
    engine: AsyncEngine
    master_key: str
    outage: bool = False
    latency_ms: int = 0
    cost_per_request: float = 1
    in_flight: dict[str, int] = field(default_factory=dict)  # R7 per-token counter

    def reset(self) -> None:
        self.outage = False
        self.latency_ms = 0
        self.cost_per_request = 1
        self.in_flight.clear()
