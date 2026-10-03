"""Адаптер MAX — заглушка по контракту (Шаг 8/14).

Официальный API ботов MAX не подтверждён (§0.4 вопрос 1 без ответа).
Канал помечается недоступным; отправка поднимает NotImplementedError
и логируется. Подключение MAX не изменит бизнес-логику.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

NOT_CONNECTED_MESSAGE = "MAX API не подключён: уточнить спецификацию (Шаг 14)"


class MaxAdapter:
    def __init__(self, api_base: str | None = None, api_token: str | None = None):
        self.api_base = api_base
        self.api_token = api_token

    def is_available(self) -> bool:
        return False

    def send(self, message_text: str, recipient_address: str) -> str:
        logger.error("MAX send отклонён (канал недоступен): recipient=%s", recipient_address)
        raise NotImplementedError(NOT_CONNECTED_MESSAGE)

    def send_file(
        self, recipient_address: str, file_path: str, caption: str | None = None
    ) -> str:
        logger.error(
            "MAX send_file отклонён (канал недоступен): recipient=%s file=%s",
            recipient_address,
            file_path,
        )
        raise NotImplementedError(NOT_CONNECTED_MESSAGE)
