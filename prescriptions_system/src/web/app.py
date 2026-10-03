"""FastAPI-приложение (Шаг 9): сессии, RBAC-обработчики, роутеры, статика.

Локальный запуск без CDN: стили и future-библиотеки — только из static/.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from src.config import get_settings
from src.db.engine import get_session_factory
from src.web.routes import admin, auth, prescriptions
from src.web.security import MustChangePasswordError

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app.name, debug=settings.app.debug)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.web_secret_key,
        session_cookie="ps_session",
        https_only=settings.app.debug is False and False,
    )
    app.state.session_factory = get_session_factory()

    app.include_router(auth.router)
    app.include_router(prescriptions.router)
    app.include_router(admin.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 401:
            return RedirectResponse("/login", status_code=303)
        if exc.status_code == 403:
            return HTMLResponse(
                "<h1>403</h1><p>Доступ запрещён.</p><p><a href='/prescriptions'>В реестр</a></p>",
                status_code=403,
            )
        return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)

    @app.exception_handler(MustChangePasswordError)
    async def must_change_password_handler(request: Request, exc):
        return RedirectResponse("/password", status_code=303)

    @app.get("/")
    def root():
        return RedirectResponse("/prescriptions", status_code=303)

    return app


app = create_app()
