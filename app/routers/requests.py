from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.requests import DecideIn, RequestIn
from app.services import requests
from app.writes import Write, write_response

router = APIRouter()


@router.post("/requests")
async def create(body: RequestIn, ctx: Write, principal: CurrentPrincipal, req: Request):
    entities = await requests.create(ctx, principal, req.app.state.directory, body)
    return await write_response(ctx, entities)


@router.get("/requests")
async def list_requests(
    principal: CurrentPrincipal,
    db: Db,
    box: str = "mine",
    type: str | None = None,
    status: str | None = None,
):
    return await requests.list_requests(db, principal, box, type, status)


@router.get("/requests/{request_id}")
async def get(request_id: int, principal: CurrentPrincipal, db: Db):
    return await requests.get(db, principal, request_id)


@router.post("/requests/{request_id}/decide")
async def decide(
    request_id: int, body: DecideIn, ctx: Write, principal: CurrentPrincipal, req: Request
):
    entities = await requests.decide(ctx, principal, req.app.state.directory, request_id, body)
    return await write_response(ctx, entities)
