"""Тесты OCR-пайплайна (Шаг 5): создание, идемпотентность, скан-фолбэк."""

from __future__ import annotations

import html
from datetime import date

import fitz
import pytest
from sqlalchemy import func, select

from src.db.models import (
    Object,
    Prescription,
    PrescriptionStatus as PS,
    PrescriptionType as PT,
    ProcessedFile,
    ProcessedFileStatus,
)
from src.ocr import pipeline

SAMPLE_LINES = [
    "ПРЕДПИСАНИЕ № П-14/2026 от 01.09.2026",
    "Объект: Склад №3",
    "Подрядчик: ООО СтройМонтаж",
    "Выявлены нарушения: Отсутствие защитного слоя бетона в плите перекрытия",
    "1. Обеспечить защитный слой бетона в пролете 2",
    "2. Представить акт скрытых работ",
    "Основание: СП 63.13330.2018, СНиП 12-03-2001",
    "Срок устранения: 30.09.2026",
]


def make_pdf(path, lines: list[str]) -> "pathlib":  # noqa: F821
    import pathlib as _pl

    path = _pl.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
    page.insert_htmlbox(fitz.Rect(36, 36, 559, 806), f"<div>{body}</div>")
    doc.save(str(path))
    doc.close()
    return path


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_pipeline_creates_prescription(db, obj, contractor, tmp_storage, tmp_path):
    pdf = make_pdf(tmp_path / "drop" / "scan01.pdf", SAMPLE_LINES)
    res = pipeline.process_file(db, pdf, object_id=obj.id, contractor_id=contractor.id)
    assert res.status == "created", res.error

    p = db.get(Prescription, res.prescription_id)
    assert p.prescription_type == PT.CUSTOMER
    assert p.status == PS.NEW
    assert p.requires_review is True
    assert p.confirmed_at is None
    assert p.doc_number == "П-14/2026"
    assert p.doc_date == date(2026, 9, 1)
    assert p.deadline == date(2026, 9, 30)
    assert p.object_id == obj.id and p.contractor_id == contractor.id
    assert p.ocr_text and "П-14/2026" in p.ocr_text
    assert p.source_file_hash == res.sha256
    assert len(p.items) == 2
    assert "СП 63.13330.2018" in (p.items[0].explicit_norm_refs or [])

    # оригинал не удалён; копия в storage и в processed
    assert pdf.exists()
    assert (tmp_storage / "storage" / "inbox_originals" / f"{res.sha256[:32]}.pdf").exists()
    assert (tmp_storage / "processed" / f"{res.sha256[:32]}.pdf").exists()

    # повторный запуск — skipped, дублей нет
    res2 = pipeline.process_file(db, pdf, object_id=obj.id, contractor_id=contractor.id)
    assert res2.status == "skipped"
    assert _count(db, Prescription) == 1
    assert _count(db, ProcessedFile) == 1


def test_pipeline_resolves_directories_by_names(db, obj, contractor, tmp_storage, tmp_path):
    pdf = make_pdf(tmp_path / "drop" / "scan02.pdf", SAMPLE_LINES)
    res = pipeline.process_file(db, pdf)
    assert res.status == "created", res.error
    p = db.get(Prescription, res.prescription_id)
    assert p.object_id == obj.id
    assert p.contractor_id == contractor.id


def test_pipeline_pending_directory_until_reference_added(db, contractor, tmp_storage, tmp_path):
    pdf = make_pdf(
        tmp_path / "drop" / "scan03.pdf",
        [line.replace("Склад №3", "Новый Бокс 9") for line in SAMPLE_LINES],
    )
    res = pipeline.process_file(db, pdf)
    assert res.status == "pending_directory"
    assert _count(db, ProcessedFile) == 0
    assert _count(db, Prescription) == 0

    db.add(Object(name="Новый Бокс 9"))
    db.commit()
    res2 = pipeline.process_file(db, pdf)
    assert res2.status == "created"


def test_pipeline_missing_requisites_marked_error(db, obj, contractor, tmp_storage, tmp_path):
    pdf = make_pdf(
        tmp_path / "drop" / "scan04.pdf",
        ["Важная бумага без номера и без реквизитов предписания строительного контроля"],
    )
    res = pipeline.process_file(db, pdf, object_id=obj.id, contractor_id=contractor.id)
    assert res.status == "incomplete"
    record = db.scalar(select(ProcessedFile))
    assert record is not None and record.status == ProcessedFileStatus.ERROR
    assert _count(db, Prescription) == 0
    # повторный пропуск — без дублей и без повторной обработки
    assert pipeline.process_file(db, pdf, object_id=obj.id, contractor_id=contractor.id).status == "skipped"


def test_pipeline_scan_fallback_uses_ocr_engine(db, obj, contractor, tmp_storage, tmp_path, monkeypatch):
    doc = fitz.open()
    doc.new_page()  # пустая страница: текстового слоя нет → скан
    scan_pdf = tmp_path / "drop_scan.pdf"
    scan_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(scan_pdf))
    doc.close()

    monkeypatch.setattr(
        pipeline,
        "ocr_scan",
        lambda path: pipeline.TextExtraction(
            text="\n".join(SAMPLE_LINES), confidence=0.91, engine="paddleocr", page_count=1
        ),
    )
    res = pipeline.process_file(db, scan_pdf, object_id=obj.id, contractor_id=contractor.id)
    assert res.status == "created", res.error
    p = db.get(Prescription, res.prescription_id)
    assert p.ocr_confidence == pytest.approx(0.91)


def test_pipeline_same_requisites_different_file_no_duplicate(db, obj, contractor, tmp_storage, tmp_path):
    a = make_pdf(tmp_path / "drop" / "a.pdf", SAMPLE_LINES)
    b = make_pdf(tmp_path / "drop" / "b.pdf", SAMPLE_LINES + ["Приложение: фото 1"])
    assert pipeline.process_file(db, a, object_id=obj.id, contractor_id=contractor.id).status == "created"
    res_b = pipeline.process_file(db, b, object_id=obj.id, contractor_id=contractor.id)
    assert res_b.status == "skipped"
    assert res_b.prescription_id is not None
    assert _count(db, Prescription) == 1


def test_run_pipeline_batch(db, obj, contractor, tmp_storage, tmp_path):
    inbox = tmp_path / "inbox_batch"
    make_pdf(inbox / "x1.pdf", SAMPLE_LINES)
    make_pdf(inbox / "x2.pdf", SAMPLE_LINES + ["Приложение 2"])
    (inbox / "notes.txt").write_text("не документ", encoding="utf-8")

    summary = pipeline.run_pipeline(db, inbox_dir=inbox, object_id=obj.id, contractor_id=contractor.id)
    assert summary["counters"]["total"] == 2
    assert summary["counters"]["created"] == 1
    assert summary["counters"]["skipped"] == 1  # дубль по реквизитам
    # повторный прогон: оба пропускаются
    summary2 = pipeline.run_pipeline(db, inbox_dir=inbox, object_id=obj.id, contractor_id=contractor.id)
    assert summary2["counters"]["skipped"] == 2
