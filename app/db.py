import enum
import zlib

from sqlalchemy import text
from sqlalchemy.engine import make_url
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
