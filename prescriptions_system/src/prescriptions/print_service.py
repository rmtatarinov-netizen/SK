"""Печатные версии внутреннего предписания (Шаг 4).

Версии неизменяемы: файл и запись новой редакции не перезаписывают
существующие; номер версии растёт. Оригинал хранится в
storage/print_versions/<prescription_id>/v<n>.pdf и никогда не удаляется.
Печать (получение файла) — только инспектор/администратор, с аудитом.
"""

from __future__ import annotations

import hashlib
import html as html_lib
from pathlib import Path

import fitz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import (
    Prescription,
    PrescriptionHistory,
    PrescriptionPrintVersion,
    User,
    UserRole,
)
from src.notifications.enums import NotificationEvent
from src.notifications.service import create_notification_outbox_records


class PrintImmutableError(RuntimeError):
    """Попытка перезаписать существующую печатную версию."""


def _require_inspector_or_admin(db: Session, actor_user_id: int | None) -> User:
    actor = db.get(User, actor_user_id) if actor_user_id is not None else None
    if actor is None or not actor.is_active:
        raise PermissionError("Пользователь не найден или неактивен")
    if actor.role not in (UserRole.INSPECTOR, UserRole.ADMIN):
        raise PermissionError("Печатную версию формирует инспектор или администратор")
    return actor


def version_storage_path(prescription_id: int, version_number: int) -> Path:
    settings = get_settings()
    base = settings.resolve_path(settings.storage.storage) / "print_versions"
    return base / f"prescription_{prescription_id}" / f"v{version_number}.pdf"


def _render_pdf(prescription: Prescription, version_number: int) -> bytes:
    object_name = prescription.object.name if prescription.object else "—"
    contractor_name = prescription.contractor.name if prescription.contractor else "—"
    items_html = "".join(
        f"<li><b>Пункт {item.item_number}.</b> {html_lib.escape(item.text)}"
        + (f" Срок: {item.deadline.isoformat()}." if item.deadline else "")
        + "</li>"
        for item in prescription.items
    )
    deadline = prescription.deadline.isoformat() if prescription.deadline else "не задан"
    body = f"""
        <h2>ПРЕДПИСАНИЕ строительного контроля № {html_lib.escape(prescription.doc_number)}</h2>
        <p>Дата: {prescription.doc_date.isoformat()} · Внутреннее (Генподрядчик → Подрядчик) · Версия v{version_number}</p>
        <p><b>Объект:</b> {html_lib.escape(object_name)}</p>
        <p><b>Подрядчик:</b> {html_lib.escape(contractor_name)}</p>
        <p><b>Описание нарушения:</b> {html_lib.escape(prescription.description)}</p>
        <p><b>Срок устранения:</b> {deadline}</p>
        <ol>{items_html}</ol>
        <br/><p>Инспектор: ____________________  Подрядчик: ____________________</p>
    """
    doc = fitz.open()
    page = doc.new_page()
    page.insert_htmlbox(fitz.Rect(40, 40, 555, 800), f"<div>{body}</div>")
    data = doc.tobytes(deflate=True)
    doc.close()
    return data


def generate_print_version(
    db: Session, prescription: Prescription, *, actor_user_id: int | None
) -> PrescriptionPrintVersion:
    """Сформировать новую неизменяемую печатную версию (внутреннее предписание)."""
    actor = _require_inspector_or_admin(db, actor_user_id)
    if prescription.prescription_type.value != "internal":
        raise ValueError("Печатная версия формируется только для внутренних предписаний")

    last_version = db.scalar(
        select(func.max(PrescriptionPrintVersion.version_number)).where(
            PrescriptionPrintVersion.prescription_id == prescription.id
        )
    )
    version_number = int(last_version or 0) + 1
    path = version_storage_path(prescription.id, version_number)
    if path.exists():
        raise PrintImmutableError(
            f"Файл печатной версии уже существует: {path} (не перезаписывается)"
        )

    pdf_bytes = _render_pdf(prescription, version_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)
    file_hash = hashlib.sha256(pdf_bytes).hexdigest()

    version = PrescriptionPrintVersion(
        prescription_id=prescription.id,
        version_number=version_number,
        file_path=str(path),
        file_hash=file_hash,
        created_by_user_id=actor.id,
    )
    db.add(version)
    db.add(
        PrescriptionHistory(
            prescription_id=prescription.id,
            changed_by_user_id=actor.id,
            change_type="print_version_created",
            new_value={"version": version_number, "sha256": file_hash},
        )
    )
    create_notification_outbox_records(
        db=db,
        prescription=prescription,
        event=NotificationEvent.INTERNAL_PRINT_VERSION_GENERATED,
    )
    db.commit()
    db.refresh(version)
    return version


def request_print(
    db: Session,
    prescription: Prescription,
    *,
    actor_user_id: int | None,
    version_id: int | None = None,
) -> PrescriptionPrintVersion:
    """Аудит запроса печати; возвращает версию (последнюю, если не указана)."""
    actor = _require_inspector_or_admin(db, actor_user_id)

    stmt = select(PrescriptionPrintVersion).where(
        PrescriptionPrintVersion.prescription_id == prescription.id
    )
    if version_id is not None:
        stmt = stmt.where(PrescriptionPrintVersion.id == version_id)
    else:
        stmt = stmt.order_by(PrescriptionPrintVersion.version_number.desc()).limit(1)
    version = db.scalar(stmt)
    if version is None:
        raise ValueError("Печатная версия не сформирована")

    db.add(
        PrescriptionHistory(
            prescription_id=prescription.id,
            changed_by_user_id=actor.id,
            change_type="print_requested",
            new_value={"version": version.version_number},
        )
    )
    db.commit()
    db.refresh(version)
    return version
