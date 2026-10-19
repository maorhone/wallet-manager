from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.auth.session import start_session
from app.errors import ApiError
from app.permissions import load_bindings

router = APIRouter(prefix="/auth/dev")


class DevLogin(BaseModel):
    upn: str


@router.get("/users")
async def dev_users(request: Request):
    directory = request.app.state.directory
    out = []
    async with request.app.state.session_factory() as db:
        for upn in directory.users:
            bindings = await load_bindings(db, directory.groups_of(upn))
            out.append(
                {
                    "upn": upn,
                    "display_name": directory.display_name(upn),
                    "roles": sorted({b.role for b in bindings}),
                }
            )
    return out


@router.post("/login")
async def dev_login(body: DevLogin, request: Request, response: Response):
    directory = request.app.state.directory
    if body.upn not in directory.users:
        raise ApiError(404, "NOT_FOUND", "Unknown dev user")
    await start_session(
        request, response, body.upn, directory.display_name(body.upn) or body.upn,
        directory.groups_of(body.upn),
    )  # fmt: skip
    return {"upn": body.upn}
