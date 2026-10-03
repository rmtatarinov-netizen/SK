"""Fake-транспорт бота для тестов (Шаг 10). Сеть не используется."""

from __future__ import annotations


class FakeBot:
    def __init__(self, photos: dict[str, tuple[bytes, str]] | None = None):
        self.sent: list[tuple[int, str, dict | None]] = []
        self.answers: list[tuple[str, str | None]] = []
        self.photos = photos if photos is not None else {
            "f1": (b"fake-jpeg-bytes", "photo.jpg")
        }

    def send(self, chat_id: int, text: str, keyboard: dict | None = None) -> None:
        self.sent.append((chat_id, text, keyboard))

    def answer(self, callback_id: str, text: str | None = None) -> None:
        self.answers.append((callback_id, text))

    def fetch_photo(self, file_id: str) -> tuple[bytes, str]:
        return self.photos[file_id]

    @property
    def last_text(self) -> str:
        return self.sent[-1][1]

    @property
    def last_keyboard(self) -> dict | None:
        return self.sent[-1][2]
