"""FastAPI application factory. Serves the API under /api and the RTL frontend at /."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings, load_settings
from .db import Database
from .routers import audit_log, auth, health, org, users
from .seed import seed

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-itadm-request"
# Folders of the copied frontend that must never be served over HTTP.
BLOCKED_PREFIXES = ("/scripts/",)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="IT Administration API", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.state.settings = settings
    app.state.db = Database(settings.database_url)
    app.state.db.create_all()
    with app.state.db.SessionLocal() as s:
        seed(s)

    @app.middleware("http")
    async def guards(request: Request, call_next):  # noqa: ANN001, ANN202
        path = request.url.path
        if any(path.startswith(p) for p in BLOCKED_PREFIXES):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # CSRF: state-changing API calls must carry a custom header, which a
        # cross-site form/page cannot send without a CORS preflight we never allow.
        if path.startswith("/api/") and request.method in UNSAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": "طلب مرفوض (CSRF header مفقود)"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    from .modules import assets, automation, files, finance, journey, migration, ops, technical
    for r in (health.router, auth.router, users.router, org.router, audit_log.router, files.router, assets.router,
              technical.router, journey.router, finance.router, ops.router, automation.router, migration.router):
        app.include_router(r)

    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app
