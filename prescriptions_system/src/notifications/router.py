"""Маршрутизация уведомлений по каналам (Шаг 8).

Правило выбора: предпочтительный доступный канал получателя → он;
нет → другой доступный; нет ни одного → None (статус no_channel).
MAX пока недоступен (заглушка), фактически используется Telegram.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Notification, RecipientChannel, User
from src.notifications.adapters.base import NotificationAdapter
from src.notifications.adapters.max import MaxAdapter
from src.notifications.adapters.telegram import TelegramAdapter
from src.notifications.enums import NotificationChannel

_adapters: dict[NotificationChannel, NotificationAdapter] = {}


def get_adapter(channel: NotificationChannel) -> NotificationAdapter:
    if channel not in _adapters:
        if channel == NotificationChannel.TELEGRAM:
            _adapters[channel] = TelegramAdapter()
        elif channel == NotificationChannel.MAX:
            _adapters[channel] = MaxAdapter()
        else:
            raise ValueError(f"Неизвестный канал: {channel}")
    return _adapters[channel]


def reset_adapters() -> None:
    """Для тестов: пересоздать адаптеры после смены конфига."""
    _adapters.clear()


def candidate_channels(
    db: Session, notification: Notification
) -> list[tuple[NotificationChannel, str]]:
    """Список (канал, адрес) для получателя уведомления, в порядке предпочтения."""
    if notification.recipient_type != "user":
        # v1 получатели — пользователи; подрядчик как сущность адресуется
        # через своих ответственных (см. resolve_recipients).
        return []

    rows = db.scalars(
        select(RecipientChannel).where(
            RecipientChannel.recipient_type == notification.recipient_type,
            RecipientChannel.recipient_id == notification.recipient_id,
            RecipientChannel.is_active.is_(True),
        )
    ).all()
    user = db.get(User, notification.recipient_id)
    preferred = user.preferred_notification_channel if user else None

    def sort_key(row: RecipientChannel):
        return (
            not row.is_preferred,
            row.channel != preferred,
            row.id,
        )

    candidates: list[tuple[NotificationChannel, str]] = []
    for row in sorted(rows, key=sort_key):
        adapter = get_adapter(row.channel)
        if adapter.is_available():
            candidates.append((row.channel, row.address))
    return candidates


def resolve_channel(
    db: Session, notification: Notification
) -> tuple[NotificationChannel, str] | None:
    """Выбрать канал и адрес доставки либо None, если каналов нет."""
    if notification.channel is not None:
        # канал уже зафиксирован (например, вручную) — проверить доступность
        adapter = get_adapter(notification.channel)
        address = db.scalar(
            select(RecipientChannel.address).where(
                RecipientChannel.recipient_type == notification.recipient_type,
                RecipientChannel.recipient_id == notification.recipient_id,
                RecipientChannel.channel == notification.channel,
                RecipientChannel.is_active.is_(True),
            )
        )
        if adapter.is_available() and address is not None:
            return notification.channel, address
        return None
    candidates = candidate_channels(db, notification)
    return candidates[0] if candidates else None
