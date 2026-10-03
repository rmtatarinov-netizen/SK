"""Выявление ЯВНЫХ нормативных ссылок из текста (Шаг 6).

LLM только извлекает буквально указанные ссылки. Это предложения со
статусом suggested — подтверждает их инспектор, сверяя с нормативной
базой (Шаг 7). Выдуманную ссылку, отсутствующую в тексте, не сохраняем.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from src.db.models import LlmRagCallStatus, LlmRagLog, NormRefSource, NormRefStatus, Prescription, PrescriptionNormRef
from src.db.repositories import prescription_has_norm_ref
from src.llm.client import LlmCallResult
from src.llm.prompts import EXTRACT_NORM_REFS_VERSION, extract_norm_refs_prompt

logger = logging.getLogger(__name__)


class NormRefItem(BaseModel):
    doc_code: str = Field(min_length=2, max_length=120)
    item: str | None = None
    quote: str = Field(min_length=1)


class NormRefsResponse(BaseModel):
    refs: list[NormRefItem] = Field(default_factory=list)


def extract_norm_refs(
    client,
    text: str,
    *,
    db: Session | None = None,
    prescription_id: int | None = None,
    prompt_version: str = EXTRACT_NORM_REFS_VERSION,
) -> list[NormRefItem]:
    prompt = extract_norm_refs_prompt(text)
    result: LlmCallResult = client.chat_json(prompt)

    refs: list[NormRefItem] = []
    error = result.error
    if result.ok and result.data is not None:
        try:
            refs = NormRefsResponse.model_validate(result.data).refs
        except ValidationError as exc:
            error = f"схема не пройдена: {exc}"
            refs = []
    elif result.ok:
        error = "пустой ответ"

    if db is not None:
        db.add(
            LlmRagLog(
                kind="llm",
                purpose="extract_norm_refs",
                model=getattr(client, "model", "?"),
                prompt_version=prompt_version,
                prescription_id=prescription_id,
                input_text=prompt[:20000],
                output_text=(result.raw or "")[:20000],
                status=LlmRagCallStatus.OK if error is None else LlmRagCallStatus.ERROR,
                error_message=error,
                duration_ms=result.duration_ms,
            )
        )
        db.commit()
    if error:
        logger.warning("LLM extract_norm_refs fallback: %s", error)
    return refs


def save_suggested_norm_refs(
    db: Session,
    prescription: Prescription,
    refs: list[NormRefItem],
    *,
    commit: bool = True,
) -> int:
    """Сохранить ссылки как suggested (требуют проверки). Идемпотентно."""
    created = 0
    for ref in refs:
        if prescription_has_norm_ref(
            db,
            prescription_id=prescription.id,
            item_id=None,
            doc_code=ref.doc_code.strip(),
            doc_item=ref.item.strip() if ref.item else None,
        ):
            continue
        db.add(
            PrescriptionNormRef(
                prescription_id=prescription.id,
                doc_code=ref.doc_code.strip(),
                doc_item=ref.item.strip() if ref.item else None,
                fragment=ref.quote[:2000],
                source=NormRefSource.EXPLICIT,
                status=NormRefStatus.SUGGESTED,
            )
        )
        created += 1
    if created and commit:
        db.commit()
    return created
