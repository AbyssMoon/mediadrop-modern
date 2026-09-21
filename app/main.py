from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from app.access_middleware import AuthenticationGateMiddleware
from app.config import Settings, get_settings
from app.database import build_session_factory
from app.modern_database import build_modern_session_factory
from app.routes_admin import router as admin_router
from app.routes_admin_auth import router as admin_auth_router
from app.routes_api import router as api_router
from app.routes_auth import router as auth_router
from app.routes_public import router as public_router
from app.session_middleware import DynamicSessionMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine, session_factory = build_session_factory(settings)
    modern_engine, modern_session_factory = build_modern_session_factory(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.media_root.mkdir(parents=True, exist_ok=True)
        settings.image_root.mkdir(parents=True, exist_ok=True)
        yield
        engine.dispose()
        modern_engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.5.4",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.modern_engine = modern_engine
    app.state.modern_session_factory = modern_session_factory

    # Order matters: the signed session middleware is outer to the optional
    # authentication gate, so the gate can inspect request.session.
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(AuthenticationGateMiddleware)
    app.add_middleware(
        DynamicSessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="mediadrop_session",
        https_only=settings.cookie_secure,
        same_site="lax",
    )

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(admin_auth_router)
    app.include_router(api_router)
    app.include_router(public_router)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 401 and request.url.path.startswith("/admin"):
            return RedirectResponse(
                f"/login?next={request.url.path}", status_code=303
            )
        from fastapi.exception_handlers import http_exception_handler as default_handler

        return await default_handler(request, exc)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    return app


app = create_app()
