"""Локальный STT (Шаг 14): интерфейс transcribe + заглушка до подключения модели."""

from src.llm.stt.base import (
    NOT_CONNECTED_MESSAGE,
    StubTranscriber,
    Transcriber,
    TranscriptionError,
    transcribe,
)

__all__ = [
    "NOT_CONNECTED_MESSAGE",
    "StubTranscriber",
    "Transcriber",
    "TranscriptionError",
    "transcribe",
]
