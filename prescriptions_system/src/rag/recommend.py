"""Подбор нормативных пунктов через RAG + LLM (Шаг 7).

LLM выбирает ТОЛЬКО переданные фрагменты (по index); код документа
берётся из метаданных, а не из ответа модели. Дополнительно проверяется,
что код существует в norm_docs — ссылка, отсутствующая в загруженной базе,
не будет выдана никогда. Результат — только suggested, подтверждает
инспектор (отделение предложений от подтверждённых).

Текст обоснования (build_justification) собирается из тех же предложений:
код, пункт, причина и дословная выдержка фрагмента. Дополнительного
вызова LLM не требуется — обоснование проверяемо по исходной норме.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    LlmRagCallStatus,
    LlmRagLog,
    NormDoc,
    NormRefSource,
    NormRefStatus,
    Prescription,
    PrescriptionNormRef,
)
from src.db.repositories import prescription_has_norm_ref
from src.llm.prompts import RECOMMEND_NORMS_VERSION, recommend_norms_prompt
from src.rag.search import search_topk

logger = logging.getLogger(__name__)


@dataclass
class ProposedNormRef:
    doc_code: str
    item: str | None
    fragment: str
    chunk_id: int | None
    reason: str | None
    distance: float | None


def recommend_for_text(
    client,
    collection,
    query_text: str,
    *,
    db: Session | None = None,
    top_k: int | None = None,
    prompt_version: str = RECOMMEND_NORMS_VERSION,
    prescription_id: int | None = None,
) -> list[ProposedNormRef]:
    hits = search_topk(collection, query_text, top_k)
    if not hits:
        return []

    fragments = [
        {
            "doc_code": hit.get("code"),
            "item": hit.get("item"),
            "text": (hit.get("fragment") or "")[:1500],
        }
        for hit in hits
    ]
    prompt = recommend_norms_prompt(query_text, fragments)
    result = client.chat_json(prompt)
    error = result.error

    matches: list[ProposedNormRef] = []
    if result.ok and result.data is not None:
        raw = result.data.get("matches")
        if not isinstance(raw, list):
            raw = []
        for match in raw[:3]:
            try:
                index = int(match.get("index"))
            except (TypeError, ValueError, AttributeError):
                continue
            if not 0 <= index < len(hits):
                continue
            hit = hits[index]
            code = (hit.get("code") or "").strip()
            if not code:
                continue
            if db is not None:
                in_base = db.scalar(
                    select(NormDoc.id).where(NormDoc.code == code).limit(1)
                )
                if in_base is None:
                    logger.warning(
                        "RAG: link dropped, code not in loaded base: %s", code
                    )
                    continue
            matches.append(
                ProposedNormRef(
                    doc_code=code,
                    item=hit.get("item") or None,
                    fragment=hit.get("fragment") or "",
                    chunk_id=hit.get("chunk_id"),
                    reason=(match.get("reason") or None),
                    distance=hit.get("distance"),
                )
            )

    if db is not None:
        db.add(
            LlmRagLog(
                kind="rag",
                purpose="recommend_norms",
                model=getattr(client, "model", "?"),
                prompt_version=prompt_version,
                prescription_id=prescription_id,
                input_text=prompt[:20000],
                output_text=(result.raw or "")[:20000],
                status=(
                    LlmRagCallStatus.OK if error is None else LlmRagCallStatus.ERROR
                ),
                error_message=error,
                duration_ms=result.duration_ms,
            )
        )
        db.commit()
    return matches


def save_proposed_refs(
    db: Session,
    prescription: Prescription,
    proposals: list[ProposedNormRef],
    *,
    item_id: int | None = None,
    commit: bool = True,
) -> int:
    """Сохранить предложения как suggested (идемпотентно)."""
    created = 0
    for proposal in proposals:
        if prescription_has_norm_ref(
            db,
            prescription_id=prescription.id,
            item_id=item_id,
            doc_code=proposal.doc_code,
            doc_item=proposal.item,
        ):
            continue
        title = db.scalar(select(NormDoc.title).where(NormDoc.code == proposal.doc_code))
        db.add(
            PrescriptionNormRef(
                prescription_id=prescription.id,
                item_id=item_id,
                doc_code=proposal.doc_code,
                doc_item=proposal.item,
                title=title,
                fragment=proposal.fragment[:2000] or None,
                chunk_id=proposal.chunk_id,
                source=NormRefSource.RAG,
                status=NormRefStatus.SUGGESTED,
            )
        )
        created += 1
    if created and commit:
        db.commit()
    return created


JUSTIFICATION_HEADER = (
    "Обоснование подобрано RAG по загруженной нормативной базе "
    "(требует проверки инспектором):"
)


def build_justification(
    refs: list[ProposedNormRef], *, max_fragment_chars: int = 300
) -> str:
    """Собрать текст обоснования из предложенных ссылок.

    Текст детерминирован: код, пункт и причина берутся из предложений RAG,
    выдержка фрагмента приводится дословно. Ничего не домысливается, поэтому
    обоснование можно сверить с исходным фрагментом нормы.
    """
    if not refs:
        return ""
    lines = [JUSTIFICATION_HEADER]
    for number, ref in enumerate(refs, start=1):
        point = f", п. {ref.item}" if ref.item else ""
        reason = f" — {ref.reason}" if ref.reason else ""
        lines.append(f"{number}. {ref.doc_code}{point}{reason}")
        fragment = (ref.fragment or "").strip()
        if fragment:
            snippet = fragment[:max_fragment_chars]
            if len(fragment) > max_fragment_chars:
                snippet = snippet.rsplit(" ", 1)[0] + "…"
            lines.append(f"   Фрагмент: «{snippet}»")
    return "\n".join(lines)


def generate_justification(
    client,
    collection,
    query_text: str,
    *,
    db: Session | None = None,
    top_k: int | None = None,
    prescription_id: int | None = None,
    max_fragment_chars: int = 300,
) -> tuple[list[ProposedNormRef], str]:
    """Подобрать нормы по тексту и собрать из них текст обоснования.

    Возвращает предложения и готовый текст. Пустой текст означает, что
    подходящих пунктов в загруженной базе не найдено (норматив не выдумывается).
    """
    refs = recommend_for_text(
        client,
        collection,
        query_text,
        db=db,
        top_k=top_k,
        prescription_id=prescription_id,
    )
    return refs, build_justification(refs, max_fragment_chars=max_fragment_chars)
