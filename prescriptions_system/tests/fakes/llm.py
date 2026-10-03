"""Fake-LLM клиент для тестов (Шаг 6/13). Сеть не используется."""

from __future__ import annotations

from src.llm.client import LlmCallResult


class FakeLLMClient:
    """Возвращает подготовленные ответы по порядку (или один и тот же).

    outcome: 'ok' — данные; 'not_json' — transport ok, битый JSON;
    'fail' — ошибка соединения.
    """

    model = "fake-model"

    def __init__(self, responses: list[dict] | dict | None = None, *, outcome: str = "ok"):
        if isinstance(responses, dict):
            responses = [responses]
        self.responses = list(responses or [])
        self.outcome = outcome
        self.prompts: list[str] = []

    def chat_json(self, prompt: str) -> LlmCallResult:
        self.prompts.append(prompt)
        if self.outcome == "fail":
            return LlmCallResult(ok=False, error="ConnectionError: fake down", duration_ms=1)
        if self.outcome == "not_json":
            return LlmCallResult(ok=True, data=None, raw="<не json>", duration_ms=1)
        data = self.responses.pop(0) if self.responses else {}
        return LlmCallResult(ok=True, data=data, raw=str(data), duration_ms=2)
