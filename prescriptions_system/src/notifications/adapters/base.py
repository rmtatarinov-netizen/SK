"""Контракт адаптера канала доставки (Шаг 8).

Бизнес-логика не знает про Telegram/MAX: только NotificationAdapter.
Адаптер отвечает за авторизацию, отправку сообщения/файла, получение
external_message_id и возврат ошибки.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class AdapterError(RuntimeError):
    """Ошибка доставки, возвращаемая адаптером (нет сети, 4xx/5xx и т.п.)."""


@runtime_checkable
class NotificationAdapter(Protocol):
    def is_available(self) -> bool:
        """Канал настроен и может использоваться (напр., есть токен)."""
        ...

    def send(self, message_text: str, recipient_address: str) -> str:
        """Отправить текст; вернуть external_message_id."""
        ...

    def send_file(
        self, recipient_address: str, file_path: str, caption: str | None = None
    ) -> str:
        """Отправить файл; вернуть external_message_id."""
        ...
