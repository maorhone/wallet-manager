"""Outbox drain (SPEC §5.3/§5.4): apply pending ``litellm_write`` rows to LiteLLM in id order."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.litellm_client import LiteLLMClient, LiteLLMPermanentError, LiteLLMTransientError
from app.models import Employee, LitellmWrite
from app.outbox.desired import DesiredUnavailable, build

_METHODS = {
    "/organization/update": "organization_update",
    "/team/update": "team_update",
    "/team/member_add": "team_member_add",
    "/team/member_update": "team_member_update",
    "/user/new": "user_new",
    "/key/update": "key_update",
    "/jwt/key/mapping/new": "jwt_mapping_new",
}
# Replays after a lost response get a 4xx "already exists"; for these the desired state is met.
_ALREADY_OK = {"/user/new", "/team/member_add"}


@dataclass
class DrainResult:
    applied: int = 0
    failed: int = 0
    stopped: bool = False  # a transient error ended the drain; retry later with backoff


async def _exists(probe) -> bool:
    try:
        await probe
    except LiteLLMPermanentError as exc:
        if exc.status == 404:
            return False
        raise
    return True


async def _apply(client: LiteLLMClient, session: AsyncSession, row: LitellmWrite, body) -> None:
    ep = row.endpoint
    if ep == "/organization/new":
        if await _exists(client.organization_info(body.organization_id)):
            await client.organization_update(body)
        else:
            await client.organization_new(body)
    elif ep == "/team/new":
        if await _exists(client.team_info(body.team_id)):
            await client.team_update(body)
        else:
            await client.team_new(body)
    elif ep == "/key/generate":
        existing = (await client.key_list(body.user_id)).keys
        token = existing[0].token if existing else (await client.key_generate(body)).token
        # Only the hash is kept; the plaintext in the response is dropped (NFR5).
        (await session.get(Employee, row.entity_id)).key_token_hash = token
    else:
        try:
            await getattr(client, _METHODS[ep])(body)
        except LiteLLMPermanentError as exc:
            if not (ep in _ALREADY_OK and exc.status < 500 and "already" in str(exc.body)):
                raise


async def drain(
    sf: async_sessionmaker[AsyncSession], client: LiteLLMClient, settings: Settings
) -> DrainResult:
    result = DrainResult()
    async with sf() as s:
        ids = list(
            await s.scalars(
                select(LitellmWrite.id)
                .where(LitellmWrite.status == "pending")
                .order_by(LitellmWrite.id)
            )
        )
    for row_id in ids:
        async with sf() as s:
            row = await s.get(LitellmWrite, row_id)
            if row.status != "pending":
                continue
            try:
                body = await build(s, settings, row.endpoint, row.entity_id)
                row.payload = body.payload()
                await _apply(client, s, row, body)
                row.status, row.last_error = "applied", None
                result.applied += 1
            except LiteLLMTransientError as exc:
                row.attempts, row.last_error = row.attempts + 1, str(exc)
                await s.commit()
                result.stopped = True
                return result
            except (LiteLLMPermanentError, DesiredUnavailable) as exc:
                row.status, row.last_error = "failed", str(exc)
                result.failed += 1
            await s.commit()
    return result
