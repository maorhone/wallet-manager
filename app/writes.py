"""Shared write path (SPEC §5.3): reason, request id / replay, audit, outbox, response shape."""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import CurrentPrincipal, Db
from app.config import Settings
from app.db import LockKind, lock_parent
from app.errors import invalid_request
from app.models import AuditLog, LitellmWrite
from app.outbox.enqueue import enqueue

_NS = uuid.UUID("6f1c1b0e-6a54-4f5e-9d0c-3c9d2f6a7b10")
_RANK = {"failed": 2, "pending": 1, "applied": 0}


def plain(value):
    """JSON-safe copy of a value (Decimal -> float) for audit rows and responses."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


class ReplayError(Exception):
    def __init__(self, request_id: str):
        self.request_id = request_id


@dataclass
class WriteContext:
    db: AsyncSession
    settings: Settings
    actor_upn: str
    request_id: str
    reason: str
    _seq: int = field(default=0, init=False)
    _last: int | None = field(default=None, init=False)

    async def lock(self, kind: LockKind, id: int) -> None:
        await lock_parent(self.db, kind, id)

    def audit(self, entity: str, entity_id, before, after, actor_role: str, **context) -> None:
        self.db.add(
            AuditLog(
                actor_upn=self.actor_upn, actor_role=actor_role, entity=entity,
                entity_id=str(entity_id), before=plain(before),
                after=plain({**(after or {}), **context}), reason=self.reason,
                request_id=self.request_id,
            )
        )  # fmt: skip

    async def enqueue(
        self, entity: str, entity_id, endpoint: str, *, payload: dict | None = None,
        chain: bool = False,
    ) -> int:  # fmt: skip
        """Queue a LiteLLM call; ``chain=True`` waits for the previous row this context queued."""
        self._seq += 1
        self._last = await enqueue(
            self.db, self.settings, entity, str(entity_id), endpoint,
            f"{self.request_id}:{self._seq}", payload=payload,
            depends_on=self._last if chain else None,
        )  # fmt: skip
        return self._last


def make_request_id(actor_upn: str, key: str | None) -> str:
    """Stable per (actor, Idempotency-Key); random without a key."""
    return str(uuid.uuid5(_NS, f"{actor_upn}:{key}") if key else uuid.uuid4())


async def write_context(request: Request, principal: CurrentPrincipal, db: Db) -> WriteContext:
    reason = unquote(request.headers.get("X-Change-Reason", "")).strip()
    rid = make_request_id(principal.upn, request.headers.get("Idempotency-Key"))
    request.state.request_id = rid
    if not reason:
        raise invalid_request("X-Change-Reason header is required")
    exists = await db.scalar(select(AuditLog.id).where(AuditLog.request_id == rid).limit(1))
    if exists:
        raise ReplayError(rid)
    return WriteContext(db, request.app.state.settings, principal.upn, rid, reason)


Write = Annotated[WriteContext, Depends(write_context)]


async def sync_status(db: AsyncSession, entity: str, ids: list[str]) -> dict[str, str]:
    """Latest outbox row per (entity_id, endpoint), aggregated failed > pending > applied."""
    rows = await db.execute(
        select(LitellmWrite).where(LitellmWrite.entity == entity, LitellmWrite.entity_id.in_(ids))
        .order_by(LitellmWrite.id)
    )  # fmt: skip
    latest = {(r.entity_id, r.endpoint): r.status for r in rows.scalars()}
    out = dict.fromkeys(ids, "applied")
    for (eid, _), status in latest.items():
        if _RANK[status] > _RANK[out[eid]]:
            out[eid] = status
    return out


async def _response(db: AsyncSession, request_id: str, entities: list[dict]) -> JSONResponse:
    out = []
    for ent in entities:
        status = await sync_status(db, ent["entity"], [str(ent["id"])])
        out.append({**ent, "id": str(ent["id"]), "sync_status": status[str(ent["id"])]})
    overall = "applied" if all(e["sync_status"] == "applied" for e in out) else "pending"
    return JSONResponse(
        plain({"request_id": request_id, "status": overall, "entities": out}),
        headers={"X-Request-ID": request_id},
    )


async def write_response(ctx: WriteContext, entities: list[dict]) -> JSONResponse:
    """Commit domain + audit + outbox rows in one transaction, then describe them."""
    await ctx.db.commit()
    return await _response(ctx.db, ctx.request_id, entities)


def install_replay_handler(app: FastAPI) -> None:
    @app.exception_handler(ReplayError)
    async def _replay(request: Request, exc: ReplayError):
        async with request.app.state.session_factory() as db:
            rows = await db.execute(
                select(AuditLog).where(AuditLog.request_id == exc.request_id).order_by(AuditLog.id)
            )
            entities = [
                {"entity": r.entity, "id": r.entity_id, "values": r.after} for r in rows.scalars()
            ]
            return await _response(db, exc.request_id, entities)
