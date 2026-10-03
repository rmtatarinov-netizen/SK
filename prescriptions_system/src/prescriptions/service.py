"""Сервис предписаний (Шаг 4): создание, единая смена статуса, подтверждение
регистрации, изменение срока и ответственного.

Единственная точка смены статуса — change_status():
проверка роли/принадлежности → допустимость перехода → история → событие
уведомления (outbox) → один коммит (транзакционно).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    Prescription,
    PrescriptionHistory,
    PrescriptionItem,
    PrescriptionStatus,
    PrescriptionType,
    User,
    UserRole,
)
from src.db.repositories import find_prescription_duplicate
from src.notifications.enums import NotificationEvent
from src.notifications.service import create_notification_outbox_records
from src.prescriptions.schemas import PrescriptionCreate
from src.prescriptions.status_rules import build_status_event, validate_status_transition


def _now() -> datetime:
    """UTC без tzinfo: SQLite хранит DATETIME как строку, смещение ломает сравнения."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _require_creator(db: Session, actor_user_id: int | None) -> User:
    actor = db.get(User, actor_user_id) if actor_user_id is not None else None
    if actor is None or not actor.is_active:
        raise PermissionError("Пользователь не найден или неактивен")
    if actor.role not in (UserRole.INSPECTOR, UserRole.ADMIN):
        raise PermissionError("Предписания создают инспектор или администратор")
    return actor


def _add_history(
    db: Session,
    *,
    prescription_id: int,
    actor_id: int | None,
    change_type: str,
    old_value: dict | None = None,
    new_value: dict | None = None,
    comment: str | None = None,
) -> None:
    db.add(
        PrescriptionHistory(
            prescription_id=prescription_id,
            changed_by_user_id=actor_id,
            change_type=change_type,
            old_value=old_value,
            new_value=new_value,
            comment=comment,
        )
    )


def create_prescription(
    db: Session, data: PrescriptionCreate, *, actor_user_id: int | None
) -> Prescription:
    """Создать предписание. Внутреннее — со статусом draft_for_signature,
    предписание Заказчика (из пайплайна OCR) — new + requires_review.

    Дубли недопустимы: проверка уникального ключа
    (doc_number, doc_date, object_id, prescription_type).
    """
    actor = _require_creator(db, actor_user_id)

    duplicate = find_prescription_duplicate(
        db,
        doc_number=data.doc_number,
        doc_date=data.doc_date,
        object_id=data.object_id,
        prescription_type=data.prescription_type,
    )
    if duplicate is not None:
        raise ValueError(
            f"Такое предписание уже зарегистрировано (id={duplicate.id})"
        )

    inspector_id = data.inspector_user_id
    if inspector_id is not None and db.get(User, inspector_id) is None:
        raise ValueError("Инспектор не найден")
    if data.responsible_user_id is not None and db.get(User, data.responsible_user_id) is None:
        raise ValueError("Ответственный не найден")

    prescription = Prescription(
        prescription_type=data.prescription_type,
        doc_number=data.doc_number,
        doc_date=data.doc_date,
        object_id=data.object_id,
        contractor_id=data.contractor_id,
        description=data.description,
        deadline=data.deadline,
        responsible_user_id=data.responsible_user_id,
        inspector_user_id=inspector_id
        or (actor.id if actor.role == UserRole.INSPECTOR else None),
        status=(
            PrescriptionStatus.DRAFT_FOR_SIGNATURE
            if data.prescription_type == PrescriptionType.INTERNAL
            else PrescriptionStatus.NEW
        ),
        # Правило §1.6: OCR/LLM-данные требуют проверки; ручное создание — нет.
        requires_review=data.prescription_type == PrescriptionType.CUSTOMER,
        created_by_user_id=actor.id,
    )
    for index, item in enumerate(data.items, start=1):
        prescription.items.append(
            PrescriptionItem(
                item_number=index,
                text=item.text,
                deadline=item.deadline,
                explicit_norm_refs=item.explicit_norm_refs or None,
            )
        )

    db.add(prescription)
    db.flush()
    _add_history(
        db,
        prescription_id=prescription.id,
        actor_id=actor.id,
        change_type="created",
        new_value={
            "doc_number": prescription.doc_number,
            "doc_date": prescription.doc_date.isoformat(),
            "type": prescription.prescription_type.value,
            "status": prescription.status.value,
            "items": len(data.items),
        },
    )
    db.commit()
    db.refresh(prescription)
    return prescription


def change_status(
    db: Session,
    prescription: Prescription,
    new_status: PrescriptionStatus,
    changed_by_user: User,
    comment: str | None = None,
) -> Prescription:
    """Единая точка смены статуса (валидация → история → outbox → commit)."""
    old_status = prescription.status

    validate_status_transition(
        old_status=old_status,
        new_status=new_status,
        changed_by_user=changed_by_user,
        prescription=prescription,
        comment=comment,
    )

    prescription.status = new_status

    if new_status == PrescriptionStatus.CLOSED:
        prescription.closed_by_user_id = changed_by_user.id
        prescription.closed_at = _now()
    if (
        old_status == PrescriptionStatus.DRAFT_FOR_SIGNATURE
        and new_status == PrescriptionStatus.NEW
    ):
        # внутреннее предписание зарегистрировано инспектором после скана
        prescription.confirmed_by_user_id = changed_by_user.id
        prescription.confirmed_at = _now()
        prescription.requires_review = False

    _add_history(
        db,
        prescription_id=prescription.id,
        actor_id=changed_by_user.id,
        change_type="status_changed",
        old_value={"status": old_status.value},
        new_value={"status": new_status.value},
        comment=comment,
    )

    db.add(prescription)

    event = build_status_event(old_status=old_status, new_status=new_status)
    if event is not None:
        create_notification_outbox_records(
            db=db,
            prescription=prescription,
            event=event,
            comment=comment,
        )

    db.commit()
    db.refresh(prescription)
    return prescription


def confirm_registration(
    db: Session, prescription: Prescription, *, actor_user_id: int | None
) -> Prescription:
    """Подтверждение данных предписания Заказчика инспектором (§1.6).

    До подтверждения уведомления Подрядчику не формируются (значение
    по умолчанию §0.4). После — событие prescription_created.
    """
    actor = db.get(User, actor_user_id) if actor_user_id is not None else None
    if actor is None or not actor.is_active:
        raise PermissionError("Пользователь не найден или неактивен")
    if actor.role not in (UserRole.INSPECTOR, UserRole.ADMIN):
        raise PermissionError("Регистрацию подтверждает инспектор или администратор")
    if prescription.confirmed_at is not None:
        raise ValueError("Предписание уже подтверждено")
    if prescription.prescription_type != PrescriptionType.CUSTOMER:
        raise ValueError("Подтверждение регистрации — для предписаний Заказчика")

    prescription.confirmed_by_user_id = actor.id
    prescription.confirmed_at = _now()
    prescription.requires_review = False

    _add_history(
        db,
        prescription_id=prescription.id,
        actor_id=actor.id,
        change_type="registration_confirmed",
    )
    create_notification_outbox_records(
        db=db, prescription=prescription, event=NotificationEvent.PRESCRIPTION_CREATED
    )
    db.commit()
    db.refresh(prescription)
    return prescription


def update_deadline(
    db: Session,
    prescription: Prescription,
    new_deadline,
    *,
    actor_user_id: int | None,
    comment: str | None = None,
) -> Prescription:
    """Изменение срока: инспектор/администратор (знач. по умолчанию, §AppA Q10)."""
    actor = db.get(User, actor_user_id) if actor_user_id is not None else None
    if actor is None or not actor.is_active:
        raise PermissionError("Пользователь не найден или неактивен")
    if actor.role not in (UserRole.INSPECTOR, UserRole.ADMIN):
        raise PermissionError("Срок меняет инспектор или администратор")
    if prescription.status in (
        PrescriptionStatus.CLOSED,
        PrescriptionStatus.REJECTED,
    ) and actor.role != UserRole.ADMIN:
        raise PermissionError("Правки в закрытом предписании — только администратор")

    old = prescription.deadline
    prescription.deadline = new_deadline
    _add_history(
        db,
        prescription_id=prescription.id,
        actor_id=actor.id,
        change_type="deadline_changed",
        old_value={"deadline": old.isoformat() if old else None},
        new_value={"deadline": new_deadline.isoformat() if new_deadline else None},
        comment=comment,
    )
    create_notification_outbox_records(
        db=db, prescription=prescription, event=NotificationEvent.DEADLINE_CHANGED, comment=comment
    )
    db.commit()
    db.refresh(prescription)
    return prescription


def change_responsible(
    db: Session,
    prescription: Prescription,
    responsible_user_id: int,
    *,
    actor_user_id: int | None,
) -> Prescription:
    actor = db.get(User, actor_user_id) if actor_user_id is not None else None
    if actor is None or not actor.is_active:
        raise PermissionError("Пользователь не найден или неактивен")
    if actor.role not in (UserRole.INSPECTOR, UserRole.ADMIN):
        raise PermissionError("Ответственного назначает инспектор или администратор")
    user = db.get(User, responsible_user_id)
    if user is None or not user.is_active:
        raise ValueError("Ответственный не найден или неактивен")
    if user.contractor_id != prescription.contractor_id:
        raise ValueError("Ответственный не относится к подрядчику предписания")

    old = prescription.responsible_user_id
    prescription.responsible_user_id = responsible_user_id
    _add_history(
        db,
        prescription_id=prescription.id,
        actor_id=actor.id,
        change_type="responsible_changed",
        old_value={"responsible_user_id": old},
        new_value={"responsible_user_id": responsible_user_id},
    )
    create_notification_outbox_records(
        db=db,
        prescription=prescription,
        event=NotificationEvent.RESPONSIBLE_CHANGED,
    )
    db.commit()
    db.refresh(prescription)
    return prescription
