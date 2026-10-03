"""Тонкий слой доступа к данным. Бизнес-правила — в сервисах (Шаги 3–8).

Здесь только выборки и проверки уникальности/идемпотентности.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    Contractor,
    Notification,
    Object,
    Prescription,
    PrescriptionHistory,
    PrescriptionItem,
    PrescriptionNormRef,
    PrescriptionStatus,
    PrescriptionType,
    ProcessedFile,
    User,
)
from src.notifications.enums import NotificationEvent


def get_object_by_name(db: Session, name: str) -> Object | None:
    return db.scalar(select(Object).where(Object.name == name))


def get_contractor_by_name(db: Session, name: str) -> Contractor | None:
    return db.scalar(select(Contractor).where(Contractor.name == name))


def get_user_by_login(db: Session, login: str) -> User | None:
    return db.scalar(select(User).where(User.login == login))


def get_prescription(db: Session, prescription_id: int) -> Prescription | None:
    return db.get(Prescription, prescription_id)


def get_prescription_by_number(
    db: Session, doc_number: str
) -> Prescription | None:
    return db.scalar(
        select(Prescription)
        .where(Prescription.doc_number == doc_number)
        .order_by(Prescription.created_at.desc())
    )


def find_prescription_duplicate(
    db: Session,
    *,
    doc_number: str,
    doc_date: date,
    object_id: int,
    prescription_type: PrescriptionType,
) -> Prescription | None:
    """Проверка идемпотентности до вставки (уникальный ключ uq_prescription_doc)."""
    return db.scalar(
        select(Prescription).where(
            Prescription.doc_number == doc_number,
            Prescription.doc_date == doc_date,
            Prescription.object_id == object_id,
            Prescription.prescription_type == prescription_type,
        )
    )


def list_prescriptions(
    db: Session,
    *,
    status: PrescriptionStatus | None = None,
    contractor_id: int | None = None,
    object_id: int | None = None,
) -> list[Prescription]:
    stmt = select(Prescription)
    if status is not None:
        stmt = stmt.where(Prescription.status == status)
    if contractor_id is not None:
        stmt = stmt.where(Prescription.contractor_id == contractor_id)
    if object_id is not None:
        stmt = stmt.where(Prescription.object_id == object_id)
    return list(db.scalars(stmt.order_by(Prescription.created_at.desc())))


def list_prescription_history(
    db: Session, prescription_id: int
) -> list[PrescriptionHistory]:
    return list(
        db.scalars(
            select(PrescriptionHistory)
            .where(PrescriptionHistory.prescription_id == prescription_id)
            .order_by(PrescriptionHistory.created_at.desc())
        )
    )


def prescription_has_norm_ref(
    db: Session,
    *,
    prescription_id: int,
    item_id: int | None,
    doc_code: str,
    doc_item: str | None,
) -> bool:
    """Защита от дублей предложений LLM/RAG при повторных запусках."""
    stmt = select(PrescriptionNormRef.id).where(
        PrescriptionNormRef.prescription_id == prescription_id,
        PrescriptionNormRef.doc_code == doc_code,
    )
    if item_id is None:
        stmt = stmt.where(PrescriptionNormRef.item_id.is_(None))
    else:
        stmt = stmt.where(PrescriptionNormRef.item_id == item_id)
    if doc_item is None:
        stmt = stmt.where(PrescriptionNormRef.doc_item.is_(None))
    else:
        stmt = stmt.where(PrescriptionNormRef.doc_item == doc_item)
    return db.scalar(stmt.limit(1)) is not None


def processed_file_exists(db: Session, sha256: str) -> bool:
    return db.scalar(
        select(ProcessedFile.id).where(ProcessedFile.sha256 == sha256).limit(1)
    ) is not None


def find_notification_by_key(
    db: Session,
    *,
    prescription_id: int,
    event_type: NotificationEvent,
    recipient_type: str,
    recipient_id: int,
    event_version: int,
) -> Notification | None:
    """Идемпотентность outbox: запись с таким ключом уже существует?"""
    return db.scalar(
        select(Notification).where(
            Notification.prescription_id == prescription_id,
            Notification.event_type == event_type,
            Notification.recipient_type == recipient_type,
            Notification.recipient_id == recipient_id,
            Notification.event_version == event_version,
        )
    )


def get_items(
    db: Session, prescription_id: int
) -> list[PrescriptionItem]:
    return list(
        db.scalars(
            select(PrescriptionItem)
            .where(PrescriptionItem.prescription_id == prescription_id)
            .order_by(PrescriptionItem.item_number)
        )
    )
