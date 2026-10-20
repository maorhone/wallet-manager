from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.errors import ApiError
from app.schemas.notifications import PrefsIn
from app.services import notifications

router = APIRouter()


@router.get("/me/notifications")
async def list_notifications(principal: CurrentPrincipal, db: Db, unread: bool = False):
    return await notifications.list_for(db, principal.upn, unread)


@router.post("/me/notifications/{notification_id}/read")
async def read_notification(
    notification_id: int, request: Request, principal: CurrentPrincipal, db: Db
):
    n = await notifications.mark_read(
        db, principal.upn, notification_id, request.app.state.clock.now()
    )
    if n is None:
        raise ApiError(404, "NOT_FOUND", "Notification not found")
    await db.commit()
    return {"id": n.id, "read_at": n.read_at}


@router.put("/me/notification-prefs")
async def put_prefs(body: PrefsIn, principal: CurrentPrincipal, db: Db):
    prefs = await notifications.set_prefs(db, principal.upn, body.root)
    await db.commit()
    return prefs
