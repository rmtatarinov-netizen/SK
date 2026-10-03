"""Outbox уведомлений (начало, Шаг 4; полная маршрутизация — Шаг 8).

Правило: сообщение фиксируется в таблице notifications (pending) в том же
коммите, что и бизнес-событие. Отправку выполняет воркер (Шаг 8).

Значение по умолчанию §0.4.3 (ответ не получен): предписание от Заказчика
уведомляет Подрядчика только после подтверждения инспектором. Переключатель
system_config key «o3X1b69W76690» (value true) включает отправку до проверки.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Notification,
    Prescription,
    SystemConfig,
    User,
    UserRole,
)
from src.notifications.enums import NotificationEvent, NotificationStatus
from src.notifications.templates import render_message

SEND_BEFORE_CONFIRMATION_KEY = "send_notifications_before_confirmation"

# События, которые легитимно повторяются для одного предписания
# (первое устранение=1, возврат=2, повторное устранение=3 …): версия растёт.
# Одноразовые события всегда идут с event_version=1: повторная обработка
# того же события не создаёт дубль (уникальный ключ + проверка в outbox).
CYCLING_EVENTS = {
    NotificationEvent.RETURNED_TO_IN_WORK,
    NotificationEvent.STATUS_CHANGED_TO_FIXED_PENDING_REVIEW,
    NotificationEvent.ATTACHMENT_ADDED,
}


def contractor_notifications_allowed(
    db: Session, prescription: Prescription
) -> bool:
    if prescription.confirmed_at is not None:
        return True
    row = db.scalar(
        select(SystemConfig).where(
            SystemConfig.key == SEND_BEFORE_CONFIRMATION_KEY
        )
    )
    return bool(row is not None and row.value)


def next_event_version(
    db: Session, prescription: Prescription, event: NotificationEvent
) -> int:
    current = db.scalar(
        select(func.max(Notification.event_version)).where(
            Notification.prescription_id == prescription.id,
            Notification.event_type == event,
        )
    )
    return int(current or 0) + 1


def _contractor_recipients(
    db: Session, prescription: Prescription
) -> list[User]:
    if prescription.responsible_user_id is not None:
        user = db.get(User, prescription.responsible_user_id)
        if user is not None and user.is_active:
            return [user]
    return list(
        db.scalars(
            select(User).where(
                User.contractor_id == prescription.contractor_id,
                User.role == UserRole.CONTRACTOR_RESPONSIBLE,
                User.is_active.is_(True),
            )
        )
    )


def _inspector_recipient(db: Session, prescription: Prescription) -> User | None:
    if prescription.inspector_user_id is not None:
        user = db.get(User, prescription.inspector_user_id)
        if user is not None and user.is_active:
            return user
    return db.scalar(
        select(User)
        .where(User.role == UserRole.INSPECTOR, User.is_active.is_(True))
        .order_by(User.id)
        .limit(1)
    )


def resolve_recipients(
    db: Session, prescription: Prescription, event: NotificationEvent
) -> list[tuple[str, int]]:
    """Матрица получателей (Шаг 8) до маршрутизации каналов."""
    recipients: list[tuple[str, int]] = []
    inspector = _inspector_recipient(db, prescription)
    if inspector is not None:
        recipients.append(("user", inspector.id))

    include_contractor = event not in (
        NotificationEvent.INTERNAL_PRINT_VERSION_GENERATED,
        NotificationEvent.INTERNAL_SIGNED_SCAN_UPLOADED,
    )
    if include_contractor and contractor_notifications_allowed(db, prescription):
        for user in _contractor_recipients(db, prescription):
            if ("user", user.id) not in recipients:
                recipients.append(("user", user.id))

    if event in (NotificationEvent.DEADLINE_APPROACHING, NotificationEvent.DEADLINE_OVERDUE):
        managers = db.scalars(
            select(User).where(
                User.role == UserRole.MANAGER, User.is_active.is_(True)
            )
        )
        for manager in managers:
            if ("user", manager.id) not in recipients:
                recipients.append(("user", manager.id))

    if event == NotificationEvent.RESPONSIBLE_CHANGED and prescription.responsible_user_id:
        if ("user", prescription.responsible_user_id) not in recipients:
            recipients.append(("user", prescription.responsible_user_id))

    return recipients


def create_notification_outbox_records(
    db: Session,
    *,
    prescription: Prescription,
    event: NotificationEvent,
    comment: str | None = None,
) -> int:
    """Записать pending-уведомления в текущей транзакции (без commit).

    Дедупликация: запись с уникальным ключом
    (prescription_id, event_type, recipient_type, recipient_id,
    event_version) создаётся один раз.
    """
    from src.db.repositories import find_notification_by_key

    message_text = render_message(event, prescription, comment)
    version = None
    created = 0
    for recipient_type, recipient_id in resolve_recipients(db, prescription, event):
        if version is None:
            version = (
                next_event_version(db, prescription, event)
                if event in CYCLING_EVENTS
                else 1
            )
        existing = find_notification_by_key(
            db,
            prescription_id=prescription.id,
            event_type=event,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            event_version=version,
        )
        if existing is not None:
            continue
        db.add(
            Notification(
                prescription_id=prescription.id,
                event_type=event,
                event_version=version,
                recipient_type=recipient_type,
                recipient_id=recipient_id,
                message_text=message_text,
                status=NotificationStatus.PENDING,
            )
        )
        created += 1
    return created
