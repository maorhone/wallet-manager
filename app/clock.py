"""Injectable clock: the API and the scheduler take the time from here, not datetime.now()."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...  # aware UTC

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock:
    """Tests move it forward; ``sleep`` advances it instead of waiting."""

    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta) -> None:
        self._now += timedelta(**delta)

    def set(self, at: datetime) -> None:
        self._now = at

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds=seconds)
        await asyncio.sleep(0)
