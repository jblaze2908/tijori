"""FastAPI application factory."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from tijori.app.auth import build_auth
from tijori.app.oidc import router as oidc_router
from tijori.app.onboarding import public as public_router
from tijori.app.onboarding import router as onboarding_router
from tijori.app.routes import router
from tijori.app.uploads import router as uploads_router
from tijori.app.web import SecurityHeaders, SpaFiles
from tijori.app.writes import router as writes_router
from tijori.app.schemas import Health
from tijori.db import make_engine
from tijori.services.errors import Invalid, NotFound
from tijori.settings import Settings, get_settings


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    is_prod = settings.env == "prod"
    app = FastAPI(
        title="Tijori API",
        version="0.1.0",
        docs_url=None if is_prod else "/api/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/api/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine or make_engine(settings.database_url.get_secret_value())
    app.state.auth = build_auth(settings)
    app.state.secret_box = settings.secret_box()
    app.include_router(oidc_router)
    app.include_router(router)
    app.include_router(uploads_router)
    app.include_router(writes_router)
    app.include_router(onboarding_router)
    app.include_router(public_router)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc) or "not found"}, status_code=404)

    @app.exception_handler(Invalid)
    async def _invalid(request: Request, exc: Invalid) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/health", response_model=Health)
    def health() -> Health | JSONResponse:
        try:
            with app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)
        return Health(status="ok", database="ok")

    app.add_middleware(SecurityHeaders)
    # Last: the UI catches every GET the API routes above did not claim.
    if settings.web_dist and (settings.web_dist / "index.html").exists():
        app.mount("/", SpaFiles(settings.web_dist), name="web")
    return app
