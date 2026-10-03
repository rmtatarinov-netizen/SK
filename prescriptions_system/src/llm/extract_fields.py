"""Извлечение структурированных полей предписания из OCR-текста (Шаг 6).

Ответ LLM валидируется Pydantic-схемой; при любой ошибке — fallback None
(черновые regex-поля Шага 5 остаются, решение за инспектором).
Результат НЕ является подтверждённым: все значения требуют проверки (§1.6).
Каждый вызов фиксируется в llm_rag_logs с версией промпта.
"""

from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from src.db.models import LlmRagCallStatus, LlmRagLog
from src.llm.client import LlmCallResult
from src.llm.prompts import EXTRACT_FIELDS_VERSION, extract_fields_prompt

logger = logging.getLogger(__name__)


class ExtractedItem(BaseModel):
    text: str = Field(min_length=2)
    deadline: date | None = None
    explicit_norm_refs: list[str] = Field(default_factory=list)


class ExtractedFields(BaseModel):
    doc_number: str | None = None
    doc_date: date | None = None
    object_name: str | None = None
    contractor_name: str | None = None
    description: str | None = None
    violation: str | None = None
    deadline: date | None = None
    responsible_person: str | None = None
    items: list[ExtractedItem] = Field(default_factory=list)
    confidence: float = 0.0

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(1.0, value))

    @field_validator(
        "doc_number",
        "object_name",
        "contractor_name",
        "description",
        "violation",
        "responsible_person",
    )
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def _write_log(
    db: Session,
    *,
    client,
    prompt_version: str,
    prompt: str,
    result: LlmCallResult,
    prescription_id: int | None,
    error: str | None,
    confidence: float | None,
) -> None:
    db.add(
        LlmRagLog(
            kind="llm",
            purpose="extract_fields",
            model=getattr(client, "model", "?"),
            prompt_version=prompt_version,
            prescription_id=prescription_id,
            input_text=prompt[:20000],
            output_text=(result.raw or "")[:20000],
            status=(
                LlmRagCallStatus.OK
                if result.ok and error is None
                else LlmRagCallStatus.ERROR
            ),
            error_message=(error or result.error),
            duration_ms=result.duration_ms,
            confidence=confidence,
        )
    )
    db.commit()


def extract_fields(
    client,
    ocr_text: str,
    *,
    db: Session | None = None,
    prescription_id: int | None = None,
    prompt_version: str = EXTRACT_FIELDS_VERSION,
) -> ExtractedFields | None:
    """Один вызов LLM (без дробления) → схема. None — fallback при ошибке."""
    prompt = extract_fields_prompt(ocr_text)
    result: LlmCallResult = client.chat_json(prompt)

    fields: ExtractedFields | None = None
    error: str | None = result.error
    if result.ok and result.data is not None:
        try:
            fields = ExtractedFields.model_validate(result.data)
        except ValidationError as exc:
            error = f"схема не пройдена: {exc}"
    elif result.ok:
        error = "пустой ответ"

    if db is not None:
        _write_log(
            db,
            client=client,
            prompt_version=prompt_version,
            prompt=prompt,
            result=result,
            prescription_id=prescription_id,
            error=error,
            confidence=fields.confidence if fields is not None else None,
        )
    if fields is None:
        logger.warning("LLM extract_fields fallback: %s", error)
    return fields


def _already_applied(db: Session, prescription_id: int, prompt_version: str) -> bool:
    from sqlalchemy import func, select

    from src.db.models import PrescriptionHistory

    return (
        db.scalar(
            select(PrescriptionHistory.id)
            .where(
                PrescriptionHistory.prescription_id == prescription_id,
                PrescriptionHistory.change_type == "llm_extracted",
                func.json_extract(PrescriptionHistory.new_value, "$.prompt_version")
                == prompt_version,
            )
            .limit(1)
        )
        is not None
    )


def apply_extracted_fields(
    db: Session,
    prescription: Prescription,
    fields: ExtractedFields,
    *,
    prompt_version: str = EXTRACT_FIELDS_VERSION,
    force: bool = False,
) -> dict:
    """Заполнить черновые поля предписания результатами LLM.

    Идемпотентность по prescription_id + prompt_version: повторный вызов
    того же промпта ничего не меняет и не создаёт дублей. Подтверждённое
    инспектором предписание LLM не трогает. Значения остаются
    requires_review=True — LLM не подтверждение (§1.6).
    """
    from src.db.models import (
        PrescriptionHistory,
        PrescriptionItem,
    )

    if prescription.confirmed_at is not None:
        raise PermissionError(
            "Подтверждённое предписание не может быть изменено LLM"
        )
    if not force and _already_applied(db, prescription.id, prompt_version):
        return {"applied": [], "skipped": "already applied"}

    applied: dict[str, object] = {}
    if prescription.deadline is None and fields.deadline is not None:
        prescription.deadline = fields.deadline
        applied["deadline"] = fields.deadline.isoformat()
    if prescription.doc_date is None and fields.doc_date is not None:  # pragma: no cover
        prescription.doc_date = fields.doc_date
        applied["doc_date"] = fields.doc_date.isoformat()

    if fields.items and not prescription.items:
        for index, item in enumerate(fields.items, start=1):
            prescription.items.append(
                PrescriptionItem(
                    item_number=index,
                    text=item.text,
                    deadline=item.deadline,
                    explicit_norm_refs=item.explicit_norm_refs or None,
                )
            )
        applied["items"] = len(fields.items)

    if fields.items:
        from src.llm.extract_norm_refs import NormRefItem, save_suggested_norm_refs

        all_refs: list[str] = []
        for item in fields.items:
            for code in item.explicit_norm_refs:
                code = code.strip()
                if code and code not in all_refs:
                    all_refs.append(code)
        if all_refs:
            saved = save_suggested_norm_refs(
                db,
                prescription,
                [NormRefItem(doc_code=code, quote=code) for code in all_refs],
                commit=False,
            )
            if saved:
                applied["suggested_norm_refs"] = saved

    if applied or fields.items:
        db.add(
            PrescriptionHistory(
                prescription_id=prescription.id,
                changed_by_user_id=None,
                change_type="llm_extracted",
                new_value={
                    "prompt_version": prompt_version,
                    "confidence": fields.confidence,
                    "applied": {k: str(v) for k, v in applied.items()},
                },
            )
        )
    db.commit()
    return {"applied": applied}
