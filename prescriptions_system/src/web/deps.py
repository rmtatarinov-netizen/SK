"""Зависимости и helpers веб-слоя (Шаг 9)."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy.orm import Session

from src.db.models import PrescriptionStatus

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

FLASH_KEY = "flashes"

STATUS_LABELS: dict[str, str] = {
    PrescriptionStatus.DRAFT_FOR_SIGNATURE.value: "Черновик (на подпись)",
    PrescriptionStatus.NEW.value: "Новое",
    PrescriptionStatus.UNDER_REVIEW.value: "На проверке",
    PrescriptionStatus.IN_WORK.value: "В работе",
    PrescriptionStatus.FIXED_PENDING_REVIEW.value: "Заявлено об устранении",
    PrescriptionStatus.CLOSED.value: "Закрыто",
    PrescriptionStatus.REJECTED.value: "Отклонено",
}


def get_db(request: Request) -> Iterator[Session]:
    """Сессия БД на запрос из фабрики приложения."""
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


def flash(request: Request, message: str) -> None:
    items = list(request.session.get(FLASH_KEY, []))
    items.append(message)
    request.session[FLASH_KEY] = items


def render(request: Request, template_name: str, user=None, **context):
    """Единая точка рендера: user + flash-сообщения в каждом шаблоне."""
    flashes = request.session.pop(FLASH_KEY, None) or []
    context = {
        "user": user,
        "flashes": flashes,
        "status_labels": STATUS_LABELS,
        **context,
    }
    return templates.TemplateResponse(request, template_name, context)
