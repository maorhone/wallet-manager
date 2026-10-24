from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.transfers import TransferIn
from app.services import transfers
from app.writes import Write, WriteContext, write_response

router = APIRouter()


@router.post("/transfers/preview")
async def preview(body: TransferIn, principal: CurrentPrincipal, db: Db, req: Request):
    """Read-only: before/after for both ends; nothing is written, so no change reason is needed."""
    ctx = WriteContext(db, req.app.state.settings, principal.upn, "", "preview")
    return await transfers.preview(ctx, principal, body, req.app.state.clock)


@router.post("/transfers")
async def create(body: TransferIn, ctx: Write, principal: CurrentPrincipal, req: Request):
    entities = await transfers.create(
        ctx, principal, req.app.state.directory, body, req.app.state.clock
    )
    return await write_response(ctx, entities)


@router.get("/transfers/{transfer_id}")
async def get(transfer_id: int, principal: CurrentPrincipal, db: Db):
    return await transfers.get(db, principal, transfer_id)
