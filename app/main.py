from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from app.auth import dev, oidc
from app.auth.csrf import CsrfMiddleware
from app.auth.oidc import build_oauth
from app.auth.session import SESSION_MAX_AGE
from app.clock import Clock, SystemClock
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.directory import Directory, FixtureDirectory, make_directory
from app.errors import ApiError, install_error_handlers
from app.litellm_client import LiteLLMClient, LiteLLMReader
from app.routers import (
    audit,
    departments,
    grants,
    groups,
    initiatives,
    me,
    members,
    notifications,
    onboarding,
    placements,
    platform,
    policies,
    production,
    reports,
    requests,
    role_bindings,
    service_keys,
    transfers,
    views,
)
from app.security_headers import SecurityHeadersMiddleware
from app.writes import install_replay_handler


async def close_app(app: FastAPI) -> None:
    """Release what ``create_app`` opened: the WM engine and the LiteLLM client/reader it built.

    Injected ``litellm``/``reader`` objects belong to the caller and are left open.
    """
    owned = app.state.owned
    if owned.get("litellm") is not None:
        await owned["litellm"].aclose()
    if owned.get("reader_engine") is not None:
        await owned["reader_engine"].dispose()
    await app.state.engine.dispose()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    await close_app(app)


def create_app(
    settings: Settings | None = None,
    litellm: LiteLLMClient | None = None,
    reader: LiteLLMReader | None = None,
    clock: Clock | None = None,
    directory: Directory | None = None,
) -> FastAPI:
    """``litellm``, ``reader``, ``clock`` and ``directory`` are injected by tests; the defaults
    are real."""
    settings = settings or Settings()
    if settings.dev_jwt_secret and not settings.dev_auth:
        raise RuntimeError("DEV_JWT_SECRET is set but DEV_AUTH is off; refusing to start")
    app = FastAPI(title="Wallet Manager", lifespan=_lifespan)
    app.state.settings = settings
    owned: dict = {}
    if litellm is None:
        litellm = owned["litellm"] = LiteLLMClient(
            settings.litellm_base_url, settings.litellm_master_key
        )
    if reader is None and settings.litellm_database_url:
        owned["reader_engine"] = create_async_engine(settings.litellm_database_url)
        reader = LiteLLMReader(owned["reader_engine"])
    app.state.clock = clock or SystemClock()
    app.state.owned = owned
    app.state.litellm = litellm
    app.state.litellm_reader = reader
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.directory = directory or make_directory(settings)
    app.state.oauth = build_oauth(settings)
    install_error_handlers(app)
    install_replay_handler(app)

    # Starlette runs the last-added middleware first: session outermost, then headers, then CSRF.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="wm_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.include_router(oidc.router)
    if settings.dev_auth:
        # Dev login always lists/authenticates against the fixture dataset (PLAN §3.1), even
        # when the app's own directory_backend is "ldap", so dev auth never depends on LDAP.
        app.state.dev_directory = FixtureDirectory(settings.seed_file)
        app.include_router(dev.router)
    app.include_router(me.router)
    app.include_router(departments.router)
    app.include_router(grants.router)
    app.include_router(groups.router)
    app.include_router(initiatives.router)
    app.include_router(members.router)
    app.include_router(notifications.router)
    app.include_router(onboarding.router)
    app.include_router(placements.router)
    app.include_router(policies.router)
    app.include_router(reports.router)
    app.include_router(requests.router)
    app.include_router(platform.router)
    app.include_router(production.router)
    app.include_router(service_keys.router)
    app.include_router(transfers.router)
    app.include_router(views.router)
    app.include_router(role_bindings.router)
    app.include_router(audit.router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request):
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise ApiError(503, "NOT_READY", "Database unreachable") from exc
        return {"status": "ready"}

    return app
