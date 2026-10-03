"""Outbox-механика доставки (Шаг 8): выборка due-записей, бэккофф, статусы.

Retry-политика (конфиг notifications.retry_delays_s): 1-я попытка — +60 с,
2-я — +300 с, 3-я — +900 с, 4-я — +3600 с; далее по последнему интервалу.
После notifications.max_attempts — статус failed, запись НЕ удаляется,
в last_error сохраняется причина; администратору пишется ERROR-лог
(служебное уведомление через веб/бот подключается на Шаге 9/10).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import Notification, NotificationAttempt
from src.notifications.enums import NotificationChannel, NotificationStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def compute_next_attempt(
    attempt_count: int, *, now: datetime | None = None
) -> datetime:
    """Время следующей попытки после attempt_count неудачных попыток."""
    now = now or utcnow()
    delays = get_settings().notifications.retry_delays_s or [60]
    index = min(attempt_count - 1, len(delays) - 1)
    delay_s = delays[index] if index >= 0 else delays[-1]
    return now + timedelta(seconds=int(delay_s))


def claim_due_notifications(
    db: Session, *, now: datetime | None = None, limit: int = 50
) -> list[Notification]:
    """pending и no_channel записи, у которых наступило время попытки."""
    now = now or utcnow()
    due_condition = or_(
        Notification.next_attempt_at.is_(None),
        Notification.next_attempt_at <= now,
    )
    stmt = (
        select(Notification)
        .where(
            or_(
                and_(
                    Notification.status == NotificationStatus.PENDING,
                    due_condition,
                ),
                # no_channel перепроверяется после исправления контакта
                and_(
                    Notification.status == NotificationStatus.NO_CHANNEL,
                    Notification.next_attempt_at.is_not(None),
                    Notification.next_attempt_at <= now,
                ),
            )
        )
        .order_by(Notification.created_at, Notification.id)
        .limit(limit)
    )
    return list(db.scalars(stmt))


def mark_processing(db: Session, notification: Notification) -> None:
    notification.status = NotificationStatus.PROCESSING
    db.commit()


def mark_sent(
    db: Session,
    notification: Notification,
    *,
    channel: NotificationChannel,
    external_message_id: str,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    notification.status = NotificationStatus.SENT
    notification.channel = channel
    notification.external_message_id = external_message_id
    notification.sent_at = now
    notification.next_attempt_at = None
    notification.last_error = None
    db.add(
        NotificationAttempt(
            notification_id=notification.id,
            attempt_number=notification.attempt_count + 1,
            channel=channel,
            success=True,
            external_message_id=external_message_id,
        )
    )
    notification.attempt_count += 1
    db.commit()


def mark_failure(
    db: Session,
    notification: Notification,
    error: str,
    *,
    channel: NotificationChannel | None = None,
    now: datetime | None = None,
) -> NotificationStatus:
    """Неудачная попытка: retry с бэккоффом либо failed после лимита."""
    now = now or utcnow()
    notification.attempt_count += 1
    notification.last_error = error[:2000]
    db.add(
        NotificationAttempt(
            notification_id=notification.id,
            attempt_number=notification.attempt_count,
            channel=channel,
            success=False,
            error=error[:2000],
        )
    )
    max_attempts = get_settings().notifications.max_attempts
    if notification.attempt_count >= max_attempts:
        notification.status = NotificationStatus.FAILED
        notification.next_attempt_at = None
    else:
        notification.status = NotificationStatus.PENDING
        notification.next_attempt_at = compute_next_attempt(
            notification.attempt_count, now=now
        )
    db.commit()
    return notification.status


def mark_no_channel(
    db: Session, notification: Notification, *, now: datetime | None = None
) -> None:
    """Нет доступного канала: запись остаётся, перепроверка позже."""
    now = now or utcnow()
    notification.status = NotificationStatus.NO_CHANNEL
    notification.last_error = "Нет доступного канала получения"
    notification.next_attempt_at = now + timedelta(
        seconds=(get_settings().notifications.retry_delays_s or [3600])[-1]
    )
    db.commit()
