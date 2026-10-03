"""Приём и проверка подписанного скана внутреннего предписания (Шаг 4).

Поток: сформирована печатная версия → документ расписан на бумаге → скан
загружается, привязывается к конкретной печатной версии → инспектор
проверяет скан (OCR-сверка реквизитов подключается на Шаге 5) → только
после проверки возможен переход draft_for_signature → new.

Идемпотентность: повторная загрузка файла с тем же SHA-256 для того же
предписания не создаёт дубль вложения. Оригиналы не удаляются.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import (
    AttachmentKind,
    Prescription,
    PrescriptionAttachment,
    PrescriptionHistory,
    PrescriptionPrintVersion,
    PrescriptionStatus,
    PrescriptionType,
    User,
    UserRole,
)
from src.notifications.enums import NotificationEvent
from src.notifications.service import create_notification_outbox_records


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _require_inspector_or_admin(db: Session, actor_user_id: int | None) -> User:
    actor = db.get(User, actor_user_id) if actor_user_id is not None else None
    if actor is None or not actor.is_active:
        raise PermissionError("Пользователь не найден или неактивен")
    if actor.role not in (UserRole.INSPECTOR, UserRole.ADMIN):
        raise PermissionError(
            "Загружать и проверять подписанный скан может только инспектор "
            "или администратор"
        )
    return actor


def sha256_of(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def link_signed_scan(
    db: Session,
    prescription: Prescription,
    source_path,
    *,
    actor_user_id: int | None,
    print_version_id: int | None = None,
) -> PrescriptionAttachment:
    """Принять скан: копировать в хранилище, привязать к печатной версии.

    Повторная загрузка того же файла (по SHA-256) — без дубля.
    """
    actor = _require_inspector_or_admin(db, actor_user_id)

    if prescription.prescription_type != PrescriptionType.INTERNAL:
        raise ValueError("Подписанный скан принимается только для внутренних предписаний")
    if prescription.status != PrescriptionStatus.DRAFT_FOR_SIGNATURE:
        raise ValueError(
            "Скан принимается до регистрации предписания (статус draft_for_signature)"
        )

    source = get_settings().resolve_path(str(source_path))
    if not source.exists():
        raise ValueError(f"Файл скана не найден: {source}")
    file_hash = sha256_of(source)

    existing = db.scalar(
        select(PrescriptionAttachment).where(
            PrescriptionAttachment.prescription_id == prescription.id,
            PrescriptionAttachment.file_hash == file_hash,
        )
    )
    if existing is not None:
        return existing

    version_stmt = (
        select(PrescriptionPrintVersion)
        .where(PrescriptionPrintVersion.prescription_id == prescription.id)
        .order_by(PrescriptionPrintVersion.version_number.desc())
    )
    if print_version_id is not None:
        version_stmt = version_stmt.where(
            PrescriptionPrintVersion.id == print_version_id
        )
    version = db.scalar(version_stmt.limit(1))
    if version is None:
        raise ValueError(
            "Сначала должна быть сформирована печатная версия предписания"
        )

    settings = get_settings()
    target_dir = settings.resolve_path(settings.storage.storage) / "signed_scans"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"prescription_{prescription.id}_{version.version_number}_{file_hash[:12]}{source.suffix}"
    shutil.copy2(source, target)

    attachment = PrescriptionAttachment(
        prescription_id=prescription.id,
        kind=AttachmentKind.SCAN,
        file_path=str(target),
        file_hash=file_hash,
        print_version_id=version.id,
        size_bytes=target.stat().st_size,
        caption=f"Подписанный скан к печатной версии v{version.version_number}",
        uploaded_by_user_id=actor.id,
    )
    db.add(attachment)
    db.flush()
    prescription.signed_scan_attachment_id = attachment.id
    prescription.signed_scan_verified_at = None

    db.add(
        PrescriptionHistory(
            prescription_id=prescription.id,
            changed_by_user_id=actor.id,
            change_type="signed_scan_attached",
            new_value={
                "attachment_id": attachment.id,
                "print_version": version.version_number,
                "sha256": file_hash,
            },
        )
    )
    create_notification_outbox_records(
        db=db,
        prescription=prescription,
        event=NotificationEvent.INTERNAL_SIGNED_SCAN_UPLOADED,
    )
    db.commit()
    db.refresh(attachment)
    return attachment


def verify_signed_scan(
    db: Session, prescription: Prescription, *, actor_user_id: int | None
) -> Prescription:
    """Инспектор подтвердил наличие подписанного скана и сверку версий."""
    actor = _require_inspector_or_admin(db, actor_user_id)

    if prescription.signed_scan_attachment_id is None:
        raise ValueError("Подписанный скан не загружен")
    attachment = db.get(PrescriptionAttachment, prescription.signed_scan_attachment_id)
    if attachment is None or attachment.print_version_id is None:
        raise ValueError("Скан не привязан к печатной версии")

    prescription.signed_scan_verified_at = _now()
    db.add(
        PrescriptionHistory(
            prescription_id=prescription.id,
            changed_by_user_id=actor.id,
            change_type="signed_scan_verified",
            new_value={"attachment_id": attachment.id},
        )
    )
    create_notification_outbox_records(
        db=db,
        prescription=prescription,
        event=NotificationEvent.INTERNAL_SIGNED_SCAN_VERIFIED,
    )
    db.commit()
    db.refresh(prescription)
    return prescription
