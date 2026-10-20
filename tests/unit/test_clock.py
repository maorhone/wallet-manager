from datetime import UTC, datetime

from app.clock import FakeClock, SystemClock


async def test_fake_clock_advance_and_sleep():
    clock = FakeClock(datetime(2026, 10, 1, tzinfo=UTC))
    clock.advance(days=1, hours=2)
    assert clock.now() == datetime(2026, 10, 2, 2, tzinfo=UTC)
    await clock.sleep(90)
    assert clock.now() == datetime(2026, 10, 2, 2, 1, 30, tzinfo=UTC)
    clock.set(datetime(2027, 1, 1, tzinfo=UTC))
    assert clock.now().year == 2027
    assert SystemClock().now().tzinfo is not None
