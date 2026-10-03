"""Эмбеддинги через локальный Ollama (bge-m3) — Шаг 7.

Документы и запросы не покидают машину. Тесты подменяют embed_text
детерминированным фейком (сеть и Ollama не используются).
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache

from src.llm.client import OllamaClient


@cache
def _client() -> OllamaClient:
    return OllamaClient()


def embed_text(text: str) -> list[float]:
    """Один текст → вектор bge-m3 (по умолчанию 1024 измерения)."""
    return _client().embed(text)


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    return [embed_text(t) for t in texts]
