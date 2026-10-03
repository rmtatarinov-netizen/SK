"""Воркер доставки уведомлений (Шаг 8).

Бизнес-логика пишет только pending-записи (outbox); отправка выполняется
здесь: событие уже зафиксировано в БД — только потом адаптер.
Каждая попытка пишется в notification_attempts; записи never удаляются.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from src.db.engine import get_session_factory
from src.db.models import Notification
from src.notifications import outbox
from src.notifications.adapters.base import AdapterError, NotificationAdapter
from src.notifications.enums import NotificationChannel
from src.notifications.outbox import utcnow
from src.notifications.router import get_adapter, resolve_channel

logger = logging.getLogger(__name__)

AdapterProvider = Callable[[NotificationChannel], NotificationAdapter]


def process_notification(
    db: Session,
    notification: Notification,
    *,
    adapter_provider: AdapterProvider = get_adapter,
    now=None,
) -> str:
    """Обработать одну запись. Вердикт: sent / retry / failed / no_channel."""
    now = now or utcnow()

    resolved = resolve_channel(db, notification)
    if resolved is None:
        outbox.mark_no_channel(db, notification, now=now)
        logger.warning(
            "notification no_channel",
            extra={"notification_id": notification.id},
        )
        return "no_channel"

    channel, address = resolved
    outbox.mark_processing(db, notification)
    try:
        adapter = adapter_provider(channel)
        external_id = adapter.send(notification.message_text, address)
    except (AdapterError, NotImplementedError, ValueError) as exc:
        status = outbox.mark_failure(
            db, notification, str(exc), channel=channel, now=now
        )
        logger.error(
            "notification delivery failed",
            extra={
                "notification_id": notification.id,
                "attempt": notification.attempt_count,
                "status": status.value,
            },
        )
        return "failed" if status.value == "failed" else "retry"

    outbox.mark_sent(
        db, notification, channel=channel, external_message_id=external_id, now=now
    )
    logger.info(
        "notification sent",
        extra={
            "notification_id": notification.id,
            "channel": channel.value,
            "external_message_id": external_id,
        },
    )
    return "sent"


def process_pending_notifications(
    db: Session,
    *,
    adapter_provider: AdapterProvider = get_adapter,
    limit: int = 50,
    now=None,
) -> dict[str, int]:
    """Один цикл обработки due-записей. Возвращает счётчики исходов."""
    counters = {"sent": 0, "retry": 0, "failed": 0, "no_channel": 0}
    for notification in outbox.claim_due_notifications(db, now=now, limit=limit):
        outcome = process_notification(
            db, notification, adapter_provider=adapter_provider, now=now
        )
        counters[outcome] = counters.get(outcome, 0) + 1
    return counters


def run_forever(
    *, poll_interval_s: float = 5.0, stop_after_iterations: int | None = None
) -> None:
    """Бесконечный цикл для запуска как сервиса/задачи планировщика."""
    factory = get_session_factory()
    iteration = 0
    while True:
        db = factory()
        try:
            counters = process_pending_notifications(db)
            if any(counters.values()):
                logger.info("notification worker cycle: %s", counters)
        except Exception:
            logger.exception("notification worker cycle error")
        finally:
            db.close()
        iteration += 1
        if stop_after_iterations is not None and iteration >= stop_after_iterations:
            return
        time.sleep(poll_interval_s)
