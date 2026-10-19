import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import Settings
from app.db import to_asyncpg_url
from app.models import Base

target_metadata = Base.metadata


def _run(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _main():
    engine = create_async_engine(to_asyncpg_url(Settings().database_url))
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


asyncio.run(_main())
