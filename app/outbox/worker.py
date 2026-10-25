"""Outbox drain (SPEC §5.3/§5.4): apply pending ``litellm_write`` rows to LiteLLM in id order."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.litellm_client import (
    JwtKeyMappingDelete,
    KeyDelete,
    LiteLLMClient,
    LiteLLMPermanentError,
    LiteLLMTransientError,
    TeamMemberDelete,
    UserDelete,
)
from app.models import Employee, LitellmWrite, Request
from app.outbox.desired import DesiredUnavailable, build
from app.services.spend import SpendUnavailable
from app.services.transfers import compensate_failed_source, fence_release, held_by_incoming, settle

_METHODS = {
    "/organization/update": "organization_update",
    "/team/update": "team_update",
    "/team/member_add": "team_member_add",
    "/team/member_update": "team_member_update",
    "/user/new": "user_new",
    "/key/update": "key_update",
    "/jwt/key/mapping/new": "jwt_mapping_new",
    "/jwt/key/mapping/update": "jwt_mapping_update",
    "/key/block": "key_block",
    "/key/unblock": "key_unblock",
    "/team/member_delete": "team_member_delete",
    "/key/delete": "key_delete",
    "/user/delete": "user_delete",
    "/jwt/key/mapping/delete": "jwt_mapping_delete",
}
# Replays after a lost response get a 4xx "already exists"; for these the desired state is met.
_ALREADY_OK = {"/user/new", "/team/member_add"}
# Targets that must not follow later state changes: the payload stored at enqueue time is applied.
_FROZEN = {
    "/team/member_delete": TeamMemberDelete,
    "/key/delete": KeyDelete,
    "/user/delete": UserDelete,
    "/jwt/key/mapping/delete": JwtKeyMappingDelete,
}
_GONE_OK = set(_FROZEN)  # a 404 on a delete means it is already gone


@dataclass
class DrainResult:
    applied: int = 0
    failed: int = 0
    held: int = 0  # skipped because the row they depend on is not applied
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
        if ep in {"/key/block", "/key/unblock"}:
            # Reconciliation retries follow the current lifecycle status too.
            employee = await session.get(Employee, row.entity_id)
            ep = "/key/unblock" if employee.status == "active" else "/key/block"
        try:
            await getattr(client, _METHODS[ep])(body)
        except LiteLLMPermanentError as exc:
            already = ep in _ALREADY_OK and exc.status < 500 and "already" in str(exc.body)
            if not (already or (ep in _GONE_OK and exc.status == 404)):
                raise


async def drain(
    sf: async_sessionmaker[AsyncSession], client: LiteLLMClient, settings: Settings, reader=None
) -> DrainResult:
    result = DrainResult()
    tz = ZoneInfo(settings.app_timezone)
    async with sf() as s:  # transfers whose last write applied earlier (or whose fence was held)
        await settle(s, settings, reader, datetime.now(tz))
        await s.commit()
    async with sf() as s:
        ids = list(
            await s.scalars(
                select(LitellmWrite.id)
                .where(
                    LitellmWrite.status == "pending",
                    LitellmWrite.endpoint != "/key/service-account/generate",
                )
                .order_by(LitellmWrite.id)
            )
        )
    for row_id in ids:
        async with sf() as s:
            # The seed CLI can overlap the live worker. Hold the row through apply + commit
            # so another drain rechecks its committed status before calling LiteLLM.
            row = await s.get(LitellmWrite, row_id, with_for_update=True)
            if row is None or row.status != "pending":
                continue
            if (
                row.depends_on
                and await s.scalar(
                    select(LitellmWrite.status).where(LitellmWrite.id == row.depends_on)
                )
                != "applied"
            ):
                result.held += 1
                continue
            if await held_by_incoming(s, row):
                result.held += 1
                continue
            try:
                if not await fence_release(
                    s, settings, reader, row, datetime.now(ZoneInfo(settings.app_timezone))
                ):
                    result.failed += 1
                    await s.commit()
                    continue
            except SpendUnavailable:
                result.held += 1  # fail closed: never release credit on an unreadable spend
                continue
            try:
                if row.endpoint in _FROZEN:
                    if row.payload is None:
                        raise DesiredUnavailable(f"{row.endpoint} has no stored payload")
                    body = _FROZEN[row.endpoint](**row.payload)
                else:
                    body = await build(s, settings, row.entity, row.endpoint, row.entity_id)
                    row.payload = body.payload()
                await _apply(client, s, row, body)
                row.status, row.last_error = "applied", None
                result.applied += 1
                await settle(s, settings, reader, datetime.now(tz), row.id)
            except LiteLLMTransientError as exc:
                row.attempts, row.last_error = row.attempts + 1, str(exc)
                await s.commit()
                result.stopped = True
                return result
            except (LiteLLMPermanentError, DesiredUnavailable) as exc:
                row.status, row.last_error = "failed", str(exc)
                result.failed += 1
                await compensate_failed_source(s, row)
            await s.execute(
                update(Request)
                .where(Request.application_write_id == row.id)
                .values(status=row.status)
            )
            await s.commit()
    return result
