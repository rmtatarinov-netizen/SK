"""Учёт обработанных файлов (Шаг 5): SHA-256 и таблица processed_files.

Идемпотентность: до обработки файл проверяется по хешу; повторный запуск
любого пайплайна не создаёт дублей. Записи реестра не удаляются.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ProcessedFile, ProcessedFileStatus

_CHUNK = 1024 * 1024


def sha256_of(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def get_processed(db: Session, sha256: str) -> ProcessedFile | None:
    return db.scalar(select(ProcessedFile).where(ProcessedFile.sha256 == sha256))


def is_already_processed(db: Session, sha256: str) -> bool:
    return get_processed(db, sha256) is not None


def mark_processed(
    db: Session,
    sha256: str,
    file_path: str,
    *,
    status: ProcessedFileStatus = ProcessedFileStatus.PROCESSED,
    prescription_id: int | None = None,
    error: str | None = None,
    commit: bool = True,
) -> ProcessedFile:
    """Зафиксировать (или обновить) запись об обработке файла по хешу."""
    record = get_processed(db, sha256)
    if record is None:
        record = ProcessedFile(sha256=sha256, original_path=str(file_path))
        db.add(record)
    record.status = status
    if prescription_id is not None:
        record.prescription_id = prescription_id
    record.error = error[:2000] if error else None
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(record)
    return record
