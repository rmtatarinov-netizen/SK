"""Интерфейс и заглушка локального распознавания речи (Шаг 14).

Голосовой ввод вне MVP: аудио → локальный STT (whisper.cpp / sherpa-onnx)
→ текст → LLM-извлечение (Шаг 6) → обязательная проверка инспектором.
Реализация подключается после стабилизации текстового сценария и не меняет
бизнес-логику: результат всегда requires_review.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


class TranscriptionError(RuntimeError):
    pass


@runtime_checkable
class Transcriber(Protocol):
    def transcribe(self, wav_path: Path | str) -> str:
        """WAV/аудио → текст (локально, документы не покидают сервер)."""
        ...


NOT_CONNECTED_MESSAGE = (
    "Локальный STT не подключён (Шаг 14): установите whisper.cpp или "
    "sherpa-onnx и реализуйте Transcriber. Голосовой ввод вне MVP."
)


class StubTranscriber:
    def transcribe(self, wav_path: Path | str) -> str:
        raise NotImplementedError(NOT_CONNECTED_MESSAGE)


_default = StubTranscriber()


def transcribe(wav_path: Path | str) -> str:
    return _default.transcribe(wav_path)
