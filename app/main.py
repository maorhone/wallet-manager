from fastapi import FastAPI, Request
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.auth import dev, oidc
from app.auth.csrf import CsrfMiddleware
from app.auth.oidc import build_oauth
from app.auth.session import SESSION_MAX_AGE
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.directory import FixtureDirectory
from app.errors import ApiError, install_error_handlers
from app.routers import departments, groups, me, policies
from app.security_headers import SecurityHeadersMiddleware
from app.writes import install_replay_handler


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Wallet Manager")
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.directory = FixtureDirectory(settings.seed_file)
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
        app.include_router(dev.router)
    app.include_router(me.router)
    app.include_router(departments.router)
    app.include_router(groups.router)
    app.include_router(policies.router)

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
