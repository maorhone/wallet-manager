"""Onboarding portal endpoints (SPEC §9.2, §11.1): two ADFS tokens, no session, no CSRF."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth.portal_jwt import PortalAuth
from app.auth.session import Db
from app.errors import invalid_request
from app.services import onboarding
from app.writes import WriteContext, make_request_id, plain

router = APIRouter()


@router.post("/onboarding")
async def post_onboarding(request: Request, caller: PortalAuth, db: Db):
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise invalid_request("Idempotency-Key header is required")
    rid = request.state.request_id = make_request_id(caller.upn, key)
    ctx = WriteContext(
        db, request.app.state.settings, caller.upn, rid,
        f"first-login onboarding via {caller.client_id}",
    )  # fmt: skip
    body, created = await onboarding.onboard(
        ctx, request.app.state.directory, caller.upn, caller.client_id
    )
    await db.commit()
    return JSONResponse(
        plain(body), status_code=201 if created else 200, headers={"X-Request-ID": rid}
    )


@router.get("/onboarding")
async def get_onboarding(request: Request, caller: PortalAuth, db: Db):
    rid = make_request_id(caller.upn, None)
    request.state.request_id = rid
    body = await onboarding.onboarding_status(db, request.app.state.settings, caller.upn, rid)
    return JSONResponse(plain(body), headers={"X-Request-ID": rid})
