from fastapi import FastAPI, Request
from sqlalchemy import text

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.errors import ApiError, install_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Wallet Manager")
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    install_error_handlers(app)

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
