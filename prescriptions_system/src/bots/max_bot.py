"""MAX-бот: заготовка обработчика входящих событий (Шаг 14).

Официальный API ботов MAX не подтверждён (§0.4 вопрос 1, см.
docs/max_contract.md). Заглушка логирует событие и поднимает
NotImplementedError. После подключения API MAX события пойдут тем же
путём, что и Telegram: привязка чата через recipient_channels (channel=max)
→ сервисы (change_status / add_attachment / add_comment) → outbox.
Бизнес-логика от канала не зависит.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

NOT_CONNECTED_MESSAGE = "MAX Bot API не подтверждён: см. docs/max_contract.md (Шаг 14)"

SUPPORTED_FUTURE_COMMANDS = (
    "/start",
    "/my_prescriptions",
    "/open",
    "/in_work",
    "/fixed",
    "/comment",
    "/close",
    "/rework",
    "/reject",
)


class MaxBotStub:
    """Контракт обработчика: handle_event(raw) без изменения бизнес-слоя."""

    def handle_event(self, raw: dict) -> str:
        event_type = str(raw.get("type") or raw.get("event") or "unknown")
        logger.warning(
            "MAX событие проигнорировано (канал не подключён): %s", event_type
        )
        raise NotImplementedError(NOT_CONNECTED_MESSAGE)

    def describe_contract(self) -> dict:
        return {
            "status": "not_connected",
            "reason": NOT_CONNECTED_MESSAGE,
            "planned_commands": list(SUPPORTED_FUTURE_COMMANDS),
            "channel": "max",
        }
