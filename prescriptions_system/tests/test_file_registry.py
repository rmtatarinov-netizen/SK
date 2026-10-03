"""Тесты реестра обработанных файлов (Шаг 5): идемпотентность по SHA-256."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.db.models import ProcessedFile, ProcessedFileStatus
from src.ocr.file_registry import (
    is_already_processed,
    mark_processed,
    sha256_of,
)

@pytest.fixture()
def sample_file(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 test bytes v1")
    return p


def test_sha256_stable_and_unique(db, sample_file, tmp_path):
    sha1 = sha256_of(sample_file)
    other = tmp_path / "doc2.pdf"
    other.write_bytes(b"%PDF-1.4 test bytes v2")
    assert sha1 != sha256_of(other)
    assert len(sha1) == 64
    assert is_already_processed(db, sha1) is False


def test_mark_then_skip(db, sample_file):
    sha = sha256_of(sample_file)
    mark_processed(db, sha, str(sample_file))
    assert is_already_processed(db, sha) is True


def test_remark_updates_not_doubles(db, sample_file, prescription):
    sha = sha256_of(sample_file)
    mark_processed(db, sha, str(sample_file))
    mark_processed(
        db, sha, str(sample_file),
        prescription_id=prescription.id,
        status=ProcessedFileStatus.PROCESSED,
    )
    total = db.scalar(select(func.count()).select_from(ProcessedFile))
    assert total == 1
    record = db.scalar(select(ProcessedFile).where(ProcessedFile.sha256 == sha))
    assert record.prescription_id == prescription.id


def test_error_recorded_kept(db, sample_file):
    sha = sha256_of(sample_file)
    mark_processed(
        db, sha, str(sample_file),
        status=ProcessedFileStatus.ERROR, error="пустой текст OCR",
    )
    record = db.scalar(select(ProcessedFile).where(ProcessedFile.sha256 == sha))
    assert record.status == ProcessedFileStatus.ERROR
    assert "OCR" in record.error
    # повторный запуск по-прежнему пропускает файл
    assert is_already_processed(db, sha) is True


def test_sha_of_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        sha256_of(tmp_path / "nope.pdf")
