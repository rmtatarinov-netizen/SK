"""Тесты LLM-извлечения полей (Шаг 6): схема, fallback, лог, идемпотентность."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from src.db.models import (
    LlmRagCallStatus,
    LlmRagLog,
    NormRefSource,
    NormRefStatus,
    Prescription,
    PrescriptionHistory,
    PrescriptionNormRef,
    PrescriptionStatus as PS,
    PrescriptionType as PT,
)
from src.llm.extract_fields import ExtractedFields, apply_extracted_fields, extract_fields
from src.llm.extract_norm_refs import NormRefItem, extract_norm_refs, save_suggested_norm_refs
from src.llm.prompts import EXTRACT_FIELDS_VERSION
from src.prescriptions.schemas import PrescriptionCreate
from src.prescriptions.service import create_prescription
from tests.fakes.llm import FakeLLMClient

GOOD = {
    "doc_number": "П-14/2026",
    "doc_date": "2026-09-01",
    "object_name": "Склад №3",
    "contractor_name": "ООО СтройМонтаж",
    "description": "Отсутствует защитный слой бетона.",
    "violation": "Защитный слой",
    "deadline": "2026-09-30",
    "responsible_person": "Иванов И.И.",
    "items": [
        {
            "text": "Обеспечить защитный слой",
            "deadline": "2026-09-30",
            "explicit_norm_refs": ["СП 63.13330.2018 п.5.3"],
        }
    ],
    "confidence": 0.82,
}


def _logs(db):
    return db.scalars(select(LlmRagLog)).all()


def test_extract_fields_ok(db):
    client = FakeLLMClient([GOOD])
    fields = extract_fields(client, "OCR TEXT", db=db)
    assert isinstance(fields, ExtractedFields)
    assert fields.doc_number == "П-14/2026"
    assert fields.doc_date == date(2026, 9, 1)
    assert fields.confidence == pytest.approx(0.82)
    logs = _logs(db)
    assert len(logs) == 1
    assert logs[0].status == LlmRagCallStatus.OK
    assert logs[0].purpose == "extract_fields"
    assert logs[0].prompt_version == EXTRACT_FIELDS_VERSION
    assert logs[0].model == "fake-model"


def test_extract_fields_bad_json_fallback(db):
    client = FakeLLMClient(outcome="not_json")
    fields = extract_fields(client, "OCR", db=db)
    assert fields is None
    logs = _logs(db)
    assert len(logs) == 1
    assert logs[0].status == LlmRagCallStatus.ERROR
    assert logs[0].error_message is not None


def test_extract_fields_connection_fail_fallback(db):
    client = FakeLLMClient(outcome="fail")
    assert extract_fields(client, "OCR", db=db) is None
    assert _logs(db)[0].status == LlmRagCallStatus.ERROR


def test_extract_fields_schema_invalid(db):
    # confidence вне диапазона не ломает: Pydantic его не клампил бы до валидатора
    client = FakeLLMClient([{**GOOD, "confidence": 5.0}])
    fields = extract_fields(client, "OCR", db=db)
    assert fields is not None
    assert fields.confidence == 1.0  # clamped


def test_extract_fields_uses_client_without_db(db):
    client = FakeLLMClient([GOOD])
    fields = extract_fields(client, "OCR")
    assert fields is not None
    assert _logs(db) == []  # без db не пишем лог


def _draft_prescription(db, admin, obj, contractor) -> Prescription:
    data = PrescriptionCreate(
        prescription_type=PT.CUSTOMER,
        doc_number="DR-1/2026",
        doc_date=date(2026, 9, 1),
        object_id=obj.id,
        contractor_id=contractor.id,
        description="черновик",
        # deadline намеренно None, items пустые
    )
    p = create_prescription(db, data, actor_user_id=admin.id)
    return p


def test_apply_fills_missing_and_idempotent(db, admin, obj, contractor):
    p = _draft_prescription(db, admin, obj, contractor)
    assert p.deadline is None and len(p.items) == 0

    fields = ExtractedFields.model_validate(GOOD)
    result = apply_extracted_fields(db, p, fields, prompt_version="extract_fields_v1")
    db.refresh(p)
    assert "deadline" in result["applied"]
    assert p.deadline == date(2026, 9, 30)
    assert len(p.items) == 1
    assert p.requires_review is True

    # повторный вызов с той же версией — ничего не добавляем (идемпотентность)
    result2 = apply_extracted_fields(db, p, fields, prompt_version="extract_fields_v1")
    db.refresh(p)
    assert result2.get("skipped")
    assert len(p.items) == 1
    hist = db.scalars(
        select(PrescriptionHistory).where(
            PrescriptionHistory.prescription_id == p.id,
            PrescriptionHistory.change_type == "llm_extracted",
        )
    ).all()
    assert len(hist) == 1


def test_apply_does_not_overwrite_existing_deadline(db, admin, obj, contractor):
    data = PrescriptionCreate(
        prescription_type=PT.CUSTOMER,
        doc_number="DR-2/2026",
        doc_date=date(2026, 9, 1),
        object_id=obj.id,
        contractor_id=contractor.id,
        description="описание черновика",
        deadline=date(2026, 9, 10),
    )
    p = create_prescription(db, data, actor_user_id=admin.id)
    apply_extracted_fields(db, p, ExtractedFields.model_validate(GOOD))
    db.refresh(p)
    assert p.deadline == date(2026, 9, 10)  # существующее не затёрто


def test_apply_rejects_confirmed(db, admin, obj, contractor):
    p = _draft_prescription(db, admin, obj, contractor)
    p.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(PermissionError):
        apply_extracted_fields(db, p, ExtractedFields.model_validate(GOOD))


def test_extract_norm_refs_and_save_dedup(db, prescription):
    client = FakeLLMClient(
        [{"refs": [{"doc_code": "СП 63.13330.2018", "item": "п.5.3", "quote": "СП 63.13330.2018"}]}]
    )
    refs = extract_norm_refs(client, "текст", db=db)
    assert len(refs) == 1 and refs[0].doc_code == "СП 63.13330.2018"

    created = save_suggested_norm_refs(db, prescription, refs)
    assert created == 1
    created2 = save_suggested_norm_refs(db, prescription, refs)
    assert created2 == 0  # дедупликация
    saved = db.scalars(
        select(PrescriptionNormRef).where(
            PrescriptionNormRef.prescription_id == prescription.id
        )
    ).all()
    assert len(saved) == 1
    assert saved[0].status == NormRefStatus.SUGGESTED


def test_save_norm_refs_empty_list_ok(db, prescription):
    assert save_suggested_norm_refs(db, prescription, []) == 0
