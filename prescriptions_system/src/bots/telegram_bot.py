"""Транспорт Telegram-бота (Шаг 10): long polling Bot API через requests.

Логика команд — в src/bots/handlers.py; здесь только getUpdates/sendMessage/
answerCallbackQuery/getFile. Ошибки одного апдейта логируются и не останавливают
цикл.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from src.bots.handlers import Update, process_update
from src.config import get_settings
from src.db.engine import get_session_factory

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
LONG_POLL_TIMEOUT_S = 30


class TelegramTransport:
    def __init__(
        self,
        bot_token: str | None = None,
        *,
        api_base: str = API_BASE,
        timeout_s: int = 60,
    ) -> None:
        if bot_token is None:
            bot_token = get_settings().telegram_bot_token
        if not bot_token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN не задан (.env). Бот не может работать."
            )
        self.bot_token = bot_token
        self.api_base = api_base.rstrip("/")
        self.timeout_s = timeout_s

    def _call(self, method: str, **params: Any) -> Any:
        url = f"{self.api_base}/bot{self.bot_token}/{method}"
        response = requests.post(url, json=params, timeout=self.timeout_s)
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(f"Telegram {method}: некорректный ответ") from None
        if response.status_code != 200 or not data.get("ok"):
            raise RuntimeError(
                f"Telegram {method}: HTTP {response.status_code} "
                f"{data.get('description', 'error')}"
            )
        return data["result"]

    def get_updates(self, offset: int = 0) -> list[dict]:
        return self._call(
            "getUpdates",
            offset=offset,
            timeout=LONG_POLL_TIMEOUT_S,
            allowed_updates=["message", "callback_query"],
        )

    def send_message(
        self, chat_id: int, text: str, keyboard: dict | None = None
    ) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if keyboard:
            params["reply_markup"] = keyboard
        self._call("sendMessage", **params)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        params: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            params["text"] = text[:200]
            params["show_alert"] = False
        self._call("answerCallbackQuery", **params)

    def download_photo(self, file_id: str) -> tuple[bytes, str]:
        info = self._call("getFile", file_id=file_id)
        file_path = info["file_path"]
        url = f"{self.api_base}/file/bot{self.bot_token}/{file_path}"
        response = requests.get(url, timeout=self.timeout_s)
        response.raise_for_status()
        return response.content, file_path.rsplit("/", 1)[-1]


def parse_update(raw: dict) -> Update | None:
    if "callback_query" in raw:
        cq = raw["callback_query"]
        message = cq.get("message") or {}
        chat = message.get("chat") or {}
        return Update(
            kind="callback",
            chat_id=chat.get("id", 0),
            from_user_id=(cq.get("from") or {}).get("id"),
            callback_id=str(raw["update_id"]),
            callback_data=cq.get("data") or "",
        )
    if "message" in raw:
        message = raw["message"]
        chat = message.get("chat") or {}
        return Update(
            kind="message",
            chat_id=chat.get("id", 0),
            from_user_id=(message.get("from") or {}).get("id"),
            text=message.get("text") or message.get("caption"),
            photo_file_ids=[p["file_id"] for p in message.get("photo") or []],
        )
    return None


def process_raw_update(db, raw: dict, transport: TelegramTransport) -> None:
    update = parse_update(raw)
    if update is None:
        return
    process_update(
        db,
        update,
        send=transport.send_message,
        answer_callback=transport.answer_callback,
        fetch_photo=transport.download_photo,
    )


def run_polling(*, stop_after_cycles: int | None = None) -> int:
    """Цикл long polling. Возвращает суммарно обработанных апдейтов."""
    transport = TelegramTransport()
    factory = get_session_factory()
    offset = 0
    processed_total = 0
    cycles = 0
    while True:
        try:
            updates = transport.get_updates(offset)
        except Exception:
            logger.exception("getUpdates failed; retry in 10s")
            updates = []
        for raw in updates:
            offset = max(offset, int(raw.get("update_id", 0)) + 1)
            db = factory()
            try:
                process_raw_update(db, raw, transport)
                processed_total += 1
            except Exception:
                db.rollback()
                logger.exception("update processing failed")
            finally:
                db.close()
        cycles += 1
        if stop_after_cycles is not None and cycles >= stop_after_cycles:
            return processed_total
    # while завершается только через return
