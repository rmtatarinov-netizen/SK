"""OCR-пайплайн предписаний Заказчика (Шаг 5).

Этапы (соответствуют ТЗ):
 1. найти новые файлы в inbox;
 2. вычислить SHA-256;
 3. проверить processed_files — повтор пропускается;
 4. сохранить оригинал в storage (никогда не удаляется);
 5. постранично извлечь текст: текстовый PDF — PyMuPDF (быстрый путь),
    скан/изображение — PaddleOCR (ru, установка на Шаге 5, requirements-ocr.txt);
 6. сохранить ocr_text и confidence;
 7. черновые поля регулярными выражениями (финал — LLM Шаг 6 + инспектор);
 8. создать запись prescriptions со статусом new и requires_review=True;
 9. перенести копию в processed.

Уведомления подрядчику не создаются: регистрация и рассылка — только после
подтверждения инспектором (§1.6, безопасное значение по умолчанию §0.4.3).
Перевод new → under_review выполняет инспектор в веб-интерфейсе (Шаг 9).

Соответствие документам: предписание Заказчика не может быть зарегистрировано
без номера и даты — такие файлы помечаются ошибкой в processed_files и
оставляются в inbox для разбора (дублей повторный прогон не создаёт).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import fitz
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import (
    Contractor,
    Object,
    Prescription,
    PrescriptionType,
    ProcessedFileStatus,
)
from src.db.repositories import (
    find_prescription_duplicate,
    get_contractor_by_name,
    get_object_by_name,
)
from src.ocr import parsers
from src.ocr.file_registry import is_already_processed, mark_processed, sha256_of
from src.prescriptions.schemas import PrescriptionCreate
from src.prescriptions.service import create_prescription

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}
# Если текстовый слой тоньше этого порога на страницу — документ считаем сканом.
MIN_TEXT_CHARS_PER_PAGE = 30


@dataclass
class TextExtraction:
    text: str
    confidence: float | None
    engine: str
    page_count: int


@dataclass
class FileResult:
    file: str
    sha256: str
    status: str  # created | skipped | pending_directory | error | incomplete
    prescription_id: int | None = None
    error: str | None = None


def extract_pdf_text(path: Path) -> TextExtraction | None:
    """Быстрый путь: текстовый слой PDF. None — если похоже на скан."""
    doc = fitz.open(str(path))
    try:
        pages = [page.get_text() for page in doc]
        page_count = doc.page_count
    finally:
        doc.close()
    text = "\n".join(pages).strip()
    if page_count == 0 or len(text) < MIN_TEXT_CHARS_PER_PAGE * page_count:
        return None
    return TextExtraction(text=text, confidence=None, engine="pymupdf", page_count=page_count)


def ocr_scan(path: Path) -> TextExtraction:
    """PaddleOCR (русский) для сканов и изображений."""
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "OCR-движок не установлен: выполните "
            "pip install -r requirements-ocr.txt (Шаг 5)"
        ) from exc

    ocr = PaddleOCR(use_angle_cls=True, lang="ru", show_log=False)
    texts: list[str] = []
    confidences: list[float] = []
    images: list[Path] = []
    page_count = 0

    if path.suffix.lower() == ".pdf":
        doc = fitz.open(str(path))
        try:
            page_count = doc.page_count
            for index, page in enumerate(doc):
                tmp = path.parent / f"_ocr_page_{index}.png"
                page.get_pixmap(dpi=200).save(str(tmp))
                images.append(tmp)
        finally:
            doc.close()
    else:
        page_count = 1
        images.append(path)

    try:
        for image in images:
            lines = ocr.ocr(str(image), cls=True)
            for page_lines in lines or []:
                for _, (word, score) in page_lines or []:
                    texts.append(word)
                    confidences.append(float(score))
    finally:
        for image in images:
            if image != path:
                image.unlink(missing_ok=True)

    return TextExtraction(
        text=" ".join(texts).strip(),
        confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
        engine="paddleocr",
        page_count=page_count,
    )


def extract_document(path: Path) -> TextExtraction:
    if path.suffix.lower() == ".pdf":
        fast = extract_pdf_text(path)
        if fast is not None:
            return fast
    return ocr_scan(path)


def _store_original(db: Session, path: Path, sha: str) -> Path:
    settings = get_settings()
    target_dir = settings.resolve_path(settings.storage.storage) / "inbox_originals"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{sha[:32]}{path.suffix.lower()}"
    if not target.exists():
        shutil.copy2(path, target)
    return target


def _move_copy_to_processed(path: Path, sha: str) -> Path:
    settings = get_settings()
    target_dir = settings.resolve_path(settings.storage.processed)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{sha[:32]}{path.suffix.lower()}"
    if not target.exists():
        shutil.copy2(path, target)
    return target


def _resolve_directory(
    db: Session, name: str | None, model: type
) -> int | None:
    """Найти запись справочника по имени. Не найдено — None (файл останется
    в inbox: без mark_processed, чтобы после добавления справочника
    повторный запуск обработал его)."""
    if not name:
        return None
    lookup = (
        get_object_by_name
        if model is Object
        else get_contractor_by_name
    )
    found = lookup(db, name.strip())
    return found.id if found is not None else None


def process_file(
    db: Session,
    path: Path,
    *,
    object_id: int | None = None,
    contractor_id: int | None = None,
) -> FileResult:
    """Обработать один файл. Идемпотентно по SHA-256."""
    path = Path(path)
    name = path.name
    try:
        sha = sha256_of(path)
    except OSError as exc:
        return FileResult(file=name, sha256="", status="error", error=str(exc))

    if is_already_processed(db, sha):
        logger.info("skip already processed", extra={"file": name})
        return FileResult(file=name, sha256=sha, status="skipped")

    try:
        stored = _store_original(db, path, sha)
        extraction = extract_document(path)
        fields = parsers.parse_fields(extraction.text)

        resolved_object_id = object_id or _resolve_directory(
            db, fields.object_name, Object
        )
        resolved_contractor_id = contractor_id or _resolve_directory(
            db, fields.contractor_name, Contractor
        )

        if not fields.doc_number or fields.doc_date is None:
            mark_processed(
                db,
                sha,
                str(path),
                status=ProcessedFileStatus.ERROR,
                error="Не распознаны номер и/или дата предписания — требуется ручной ввод",
            )
            return FileResult(
                file=name, sha256=sha, status="incomplete",
                error="missing doc_number/doc_date",
            )

        if resolved_object_id is None or resolved_contractor_id is None:
            return FileResult(
                file=name, sha256=sha, status="pending_directory",
                error="Объект/подрядчик не найдены в справочнике — файл ждёт в inbox",
            )

        # защита от дубля, если хеш не зафиксирован, но предписание уже есть
        duplicate = find_prescription_duplicate(
            db,
            doc_number=fields.doc_number,
            doc_date=fields.doc_date,
            object_id=resolved_object_id,
            prescription_type=PrescriptionType.CUSTOMER,
        )
        if duplicate is not None:
            mark_processed(
                db, sha, str(path), prescription_id=duplicate.id
            )
            _move_copy_to_processed(path, sha)
            return FileResult(
                file=name, sha256=sha, status="skipped",
                prescription_id=duplicate.id,
            )

        data = PrescriptionCreate(
            prescription_type=PrescriptionType.CUSTOMER,
            doc_number=fields.doc_number,
            doc_date=fields.doc_date,
            object_id=resolved_object_id,
            contractor_id=resolved_contractor_id,
            description=fields.description or extraction.text[:1500],
            deadline=fields.deadline,
            items=[
                {
                    "text": item,
                    "deadline": fields.deadline,
                    "explicit_norm_refs": fields.norm_refs,
                }
                for item in (fields.items or [fields.description or extraction.text[:500]])
            ],
        )
        prescription = create_prescription(db, data, actor_user_id=None, as_system=True)
        prescription.ocr_text = extraction.text
        prescription.ocr_confidence = extraction.confidence
        prescription.source_file_path = str(stored)
        prescription.source_file_hash = sha
        db.commit()
        db.refresh(prescription)

        mark_processed(db, sha, str(path), prescription_id=prescription.id)
        _move_copy_to_processed(path, sha)
        logger.info(
            "prescription created from OCR",
            extra={
                "prescription_id": prescription.id,
                "engine": extraction.engine,
                "sha256": sha[:12],
            },
        )
        return FileResult(
            file=name, sha256=sha, status="created",
            prescription_id=prescription.id,
        )
    except Exception as exc:
        db.rollback()
        mark_processed(
            db, sha, str(path), status=ProcessedFileStatus.ERROR, error=str(exc)
        )
        logger.exception("OCR pipeline failed", extra={"file": name})
        return FileResult(file=name, sha256=sha, status="error", error=str(exc))


def run_pipeline(
    db: Session,
    *,
    inbox_dir: Path | str | None = None,
    object_id: int | None = None,
    contractor_id: int | None = None,
) -> dict:
    """Пакетная обработка inbox-каталога. Повторный запуск безопасен."""
    settings = get_settings()
    inbox = Path(inbox_dir) if inbox_dir else settings.resolve_path(
        f"{settings.storage.inbox}/customer"
    )
    results = []
    if inbox.exists():
        for path in sorted(inbox.iterdir()):
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                results.append(
                    process_file(
                        db, path, object_id=object_id, contractor_id=contractor_id
                    )
                )
    counters: dict[str, int] = {}
    for item in results:
        counters[item.status] = counters.get(item.status, 0) + 1
    counters["total"] = len(results)
    return {"counters": counters, "results": [item.__dict__ for item in results]}
