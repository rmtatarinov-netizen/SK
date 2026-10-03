"""Обёртка над HTTP API Ollama (Шаг 6): JSON-режим, таймауты, повторы, лог.

Слабое железо: таймаут до 300 с (из config), один запрос на предписание.
При ошибке — один повтор с уточняющей инструкцией, затем fallback (None):
черновые regex-поля (Шаг 5) сохраняются, финальное решение за инспектором.

LLM не имеет прав: подтверждать нормативные ссылки, менять статусы,
закрывать предписания, удалять данные.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests
from sqlalchemy.orm import Session

from src.config import get_settings

logger = logging.getLogger(__name__)

RETRY_INSTRUCTION = (
    "\n\nПРЕДУПРЕЖДЕНИЕ: предыдущий ответ не был корректным JSON. "
    "Верни СТРОГО JSON без пояснений, markdown и текста вне JSON."
)


class LLMError(RuntimeError):
    pass


@dataclass
class LlmCallResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None
    raw: str | None = None


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: int | None = None,
        *,
        max_attempts: int = 2,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.ollama.base_url).rstrip("/")
        self.model = model or settings.ollama.llm_model
        self.timeout_s = timeout_s or settings.ollama.timeout_s
        self.max_attempts = max(1, max_attempts)

    def _request(self, prompt: str) -> str:
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.0},
            },
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    def chat_json(self, prompt: str) -> LlmCallResult:
        """Один логический вызов с повтором при некорректном JSON."""
        started = time.monotonic()
        last_error: str | None = None
        current_prompt = prompt
        for attempt in range(1, self.max_attempts + 1):
            try:
                content = self._request(current_prompt)
                try:
                    data = json.loads(content)
                except ValueError as exc:
                    raise ValueError(f"ответ не JSON: {exc}") from exc
                if not isinstance(data, dict):
                    raise ValueError("ожидается JSON-объект верхнего уровня")
                duration_ms = int((time.monotonic() - started) * 1000)
                return LlmCallResult(
                    ok=True, data=data, raw=content, duration_ms=duration_ms
                )
            except (requests.RequestException, ValueError, KeyError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "LLM attempt %s failed: %s", attempt, last_error
                )
                current_prompt = prompt + RETRY_INSTRUCTION
        duration_ms = int((time.monotonic() - started) * 1000)
        return LlmCallResult(ok=False, error=last_error, duration_ms=duration_ms)

    def embed(self, text: str) -> list[float]:
        """Эмбеддинг через Ollama (используется RAG, Шаг 7)."""
        settings = get_settings()
        response = requests.post(
            f"{self.base_url}/api/embeddings",
            json={"model": settings.ollama.embed_model, "prompt": text},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        embedding = response.json().get("embedding")
        if not embedding:
            raise LLMError("Ollama вернула пустой embedding")
        return embedding


def log_llm_call(
    db: Session,
    *,
    purpose: str,
    model: str,
    prompt_version: str,
    prompt_text: str,
    result: LlmCallResult,
    prescription_id: int | None = None,
    confidence: float | None = None,
) -> None:
    """Каждый вызов фиксируется в llm_rag_logs (воспроизводимость)."""
    from src.db.models import LlmRagCallStatus, LlmRagLog

    db.add(
        LlmRagLog(
            kind="llm",
            purpose=purpose,
            model=model,
            prompt_version=prompt_version,
            prescription_id=prescription_id,
            input_text=prompt_text[:20000],
            output_text=(result.raw or result.error or "")[:20000],
            status=LlmRagCallStatus.OK if result.ok else LlmRagCallStatus.ERROR,
            error_message=result.error,
            duration_ms=result.duration_ms,
            confidence=confidence,
        )
    )
    db.commit()

