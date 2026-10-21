import asyncio
import enum
import zlib
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def to_asyncpg_url(uri: str, database: str | None = None) -> str:
    """Convert a plain/pgembed URI (``postgresql://u:@/db?host=/sock/dir``) to asyncpg.

    SQLAlchemy forwards the ``host=`` query parameter to asyncpg, which treats a
    directory as a unix-socket dir (verified against pgembed).
    """
    url = make_url(uri).set(drivername="postgresql+asyncpg")
    if database:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(to_asyncpg_url(url))


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class LockKind(enum.IntEnum):
    DEPARTMENT = 1  # split, allowances, group pots
    GROUP = 2  # member caps and member model caps
    PMO = 3  # Phase 2
    EMPLOYEE = 4  # member lifecycle actions on one person (id = employee_lock_id(upn))
    JOB = 5  # scheduler job locks (session-level, one replica per job)
    REQUEST = 6  # serialize replay lookup and the entire request transaction
    PLATFORM = 7  # creation registries: 0 = departments, 1 = groups
    PRODUCTION = 8  # production org budget and its systems (id = department_id)


def employee_lock_id(upn: str) -> int:
    return zlib.crc32(upn.encode()) & 0x7FFFFFFF


async def lock_parent(session: AsyncSession, kind: LockKind, id: int) -> None:
    """Transaction-scoped advisory lock; take it before reading any sums (FR-V1)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:k, :i)"), {"k": int(kind), "i": id})


async def create_database(uri: str, name: str, *, if_missing: bool = False) -> None:
    """CREATE DATABASE via asyncpg (pgembed's bundled psql segfaults in this sandbox)."""
    import asyncpg

    url = make_url(uri)
    conn = await asyncpg.connect(
        user=url.username, password=url.password, host=url.query.get("host", url.host),
        port=url.port, database=url.database,
    )  # fmt: skip
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{name}"')
        elif not if_missing:
            raise RuntimeError(f"database {name} already exists")
    finally:
        await conn.close()


# deadlock_detected, lock_not_available (lock_timeout)
LOCK_CONFLICT_SQLSTATES = frozenset({"40P01", "55P03"})


def is_lock_conflict(exc: BaseException | None) -> bool:
    """True for a deadlock or a lock_timeout error, however SQLAlchemy/asyncpg wrapped it."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc, "sqlstate", None) in LOCK_CONFLICT_SQLSTATES:
            return True
        exc = getattr(exc, "orig", None) or exc.__cause__
    return False


async def truncate_with_retry(
    engine: AsyncEngine,
    sql: str,
    *,
    attempts: int = 8,
    lock_timeout_ms: int = 5000,
    on_conflict: Callable[[AsyncEngine], Awaitable[None]] | None = None,
) -> None:
    """Run a TRUNCATE in its own short transaction; retry on a deadlock or a lock timeout.

    TRUNCATE takes ACCESS EXCLUSIVE locks table by table, so next to live readers (the dev/e2e
    stack's API, worker and scheduler) it can deadlock or queue forever behind one of them.
    A bounded ``lock_timeout`` plus a retry turns that into a short wait. ``on_conflict`` runs
    before each retry (the test suite uses it to drop leaked sessions). Dev/test use only.
    """
    for attempt in range(1, attempts + 1):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}"))
                await conn.execute(text(sql))
            return
        except DBAPIError as exc:
            if attempt == attempts or not is_lock_conflict(exc):
                raise
            if on_conflict is not None:
                await on_conflict(engine)
            await asyncio.sleep(0.2 * attempt)
