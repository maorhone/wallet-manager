"""Test-only controls, mounted when FAKE_TEST_CONTROLS=1 (the fake's default)."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .db import reset_db

router = APIRouter(prefix="/_test")


class Outage(BaseModel):
    on: bool


@router.post("/reset")
async def reset(request: Request):
    await reset_db(request.app.state.fake.engine)
    request.app.state.fake.outage = False
    return {"ok": True}


@router.post("/outage")
async def outage(body: Outage, request: Request):
    request.app.state.fake.outage = body.on
    return {"outage": body.on}
