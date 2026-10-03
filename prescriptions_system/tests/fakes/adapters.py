"""Fake-адаптеры каналов: режимы ok / fail-N-раз (Шаг 13)."""

from __future__ import annotations

from src.notifications.adapters.base import AdapterError


class FakeAdapter:
    """outcomes: список исходов вызова. 'ok' — успех; str — выброс AdapterError.

    Список расходуется; после окончания возвращается 'ok'.
    """

    def __init__(self, outcomes: list[str] | None = None, *, available: bool = True):
        self.outcomes = list(outcomes or [])
        self.sent: list[tuple[str, str]] = []
        self.available = available
        self._counter = 0

    def is_available(self) -> bool:
        return self.available

    def _next_outcome(self) -> str | None:
        if self.outcomes:
            return self.outcomes.pop(0)
        return None

    def send(self, message_text: str, recipient_address: str) -> str:
        outcome = self._next_outcome()
        if outcome and outcome != "ok":
            raise AdapterError(outcome)
        self.sent.append((recipient_address, message_text))
        self._counter += 1
        return f"fake-msg-{self._counter}"

    def send_file(
        self, recipient_address: str, file_path: str, caption: str | None = None
    ) -> str:
        return self.send(f"file:{file_path}", recipient_address)


class FakeAdapterFactory:
    """Поставщик адаптеров по каналу для worker/adapter_provider."""

    def __init__(self, adapter: FakeAdapter):
        self.adapter = adapter

    def __call__(self, channel):
        return self.adapter
