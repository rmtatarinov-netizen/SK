"""Адаптер Telegram Bot API (Шаг 8). Локальная сборка, токен из .env."""

from __future__ import annotations

from typing import Any

import requests

from src.config import get_settings
from src.notifications.adapters.base import AdapterError

DEFAULT_API_BASE = "https://api.telegram.org"
HTTP_TIMEOUT_S = 15


class TelegramAdapter:
    def __init__(
        self,
        bot_token: str | None = None,
        *,
        api_base: str | None = None,
        timeout_s: int = HTTP_TIMEOUT_S,
    ) -> None:
        if bot_token is None:
            bot_token = get_settings().telegram_bot_token
        self.bot_token = (bot_token or "").strip()
        self.api_base = (
            api_base or get_settings().telegram_api_base or DEFAULT_API_BASE
        ).rstrip("/")
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        return bool(self.bot_token)

    def _call(self, method: str, payload: dict[str, Any]) -> dict:
        if not self.is_available():
            raise AdapterError("Telegram: токен бота не настроен")
        url = f"{self.api_base}/bot{self.bot_token}/{method}"
        try:
            response = requests.post(url, json=payload, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise AdapterError(f"Telegram: сетевая ошибка: {exc}") from exc
        try:
            data = response.json()
        except ValueError:
            raise AdapterError(
                f"Telegram: некорректный ответ HTTP {response.status_code}"
            ) from None
        if response.status_code != 200 or not data.get("ok"):
            raise AdapterError(
                f"Telegram: HTTP {response.status_code}: "
                f"{data.get('description', 'unknown error')}"
            )
        return data["result"]

    def send(self, message_text: str, recipient_address: str) -> str:
        result = self._call(
            "sendMessage", {"chat_id": recipient_address, "text": message_text}
        )
        return str(result["message_id"])

    def send_file(
        self, recipient_address: str, file_path: str, caption: str | None = None
    ) -> str:
        if not self.is_available():
            raise AdapterError("Telegram: токен бота не настроен")
        with open(file_path, "rb") as fh:
            url = f"{self.api_base}/bot{self.bot_token}/sendDocument"
            payload = {"chat_id": recipient_address}
            if caption:
                payload["caption"] = caption
            try:
                response = requests.post(
                    url, data=payload, files={"document": fh}, timeout=self.timeout_s
                )
            except requests.RequestException as exc:
                raise AdapterError(f"Telegram: сетевая ошибка: {exc}") from exc
        try:
            data = response.json()
        except ValueError:
            raise AdapterError(
                f"Telegram: некорректный ответ HTTP {response.status_code}"
            ) from None
        if response.status_code != 200 or not data.get("ok"):
            raise AdapterError(
                f"Telegram: HTTP {response.status_code}: "
                f"{data.get('description', 'unknown error')}"
            )
        return str(data["result"]["message_id"])
