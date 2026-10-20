"""Test-suite DB isolation helpers."""

import warnings

from sqlalchemy import text


async def drop_leaked_sessions(eng) -> None:
    """Terminate other sessions on this database that sit inside a transaction.

    Tests run one at a time, so at fixture time any such session was leaked by an earlier
    test (an app or engine not closed) and its locks would block the TRUNCATE until
    pytest-timeout fires. Say which one it was, then drop it.
    """
    async with eng.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT pid, usename, state, left(query, 160) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                    "AND backend_type = 'client backend' AND state <> 'idle'"
                )
            )
        ).all()
        for pid, user, state, query in rows:
            warnings.warn(
                f"terminated leaked DB session pid={pid} {user} {state}: {query}", stacklevel=2
            )
            await conn.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        await conn.commit()
