"""Тесты модели данных (Шаг 2): таблицы, уникальные ключи, enum-значения, PRAGMA."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from src.db import models as m
from src.notifications.enums import (
    NotificationChannel,
    NotificationEvent,
    NotificationStatus,
)
from src.db.engine import get_engine

EXPECTED_TABLES = {
    "objects",
    "contractors",
    "users",
    "recipient_channels",
    "norm_docs",
    "prescriptions",
    "prescription_items",
    "prescription_norm_refs",
    "prescription_attachments",
    "prescription_history",
    "norm_chunks",
    "norm_chunk_embeddings",
    "notifications",
    "notification_attempts",
    "user_role_change_log",
    "llm_rag_logs",
    "processed_files",
    "system_config",
}


def test_all_tables_created(engine) -> None:
    names = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= names


def test_prescription_doc_unique_key(db, prescription: m.Prescription) -> None:
    dup = m.Prescription(
        prescription_type=prescription.prescription_type,
        doc_number=prescription.doc_number,
        doc_date=prescription.doc_date,
        object_id=prescription.object_id,
        contractor_id=prescription.contractor_id,
        description="дубль",
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_other_type_same_doc_allowed(db, prescription, obj, contractor) -> None:
    other = m.Prescription(
        prescription_type=m.PrescriptionType.INTERNAL,
        doc_number=prescription.doc_number,
        doc_date=prescription.doc_date,
        object_id=obj.id,
        contractor_id=contractor.id,
        description="внутреннее с тем же номером — допустимо",
    )
    db.add(other)
    db.commit()
    db.refresh(other)
    assert other.id is not None


def test_processed_files_sha_unique(db) -> None:
    sha = "a" * 64
    db.add(m.ProcessedFile(sha256=sha, original_path="first.pdf"))
    db.commit()
    db.add(m.ProcessedFile(sha256=sha, original_path="second.pdf"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_notification_idempotency_key(db, prescription) -> None:
    def make() -> m.Notification:
        return m.Notification(
            prescription_id=prescription.id,
            event_type=NotificationEvent.PRESCRIPTION_CREATED,
            event_version=1,
            recipient_type="user",
            recipient_id=1,
            channel=NotificationChannel.TELEGRAM,
            message_text="текст",
        )

    db.add(make())
    db.commit()
    db.add(make())
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_notification_event_version_allows_new_event(db, prescription) -> None:
    db.add(
        m.Notification(
            prescription_id=prescription.id,
            event_type=NotificationEvent.RETURNED_TO_IN_WORK,
            event_version=2,
            recipient_type="user",
            recipient_id=1,
            channel=NotificationChannel.TELEGRAM,
            message_text="первый возврат",
        )
    )
    db.add(
        m.Notification(
            prescription_id=prescription.id,
            event_type=NotificationEvent.RETURNED_TO_IN_WORK,
            event_version=3,
            recipient_type="user",
            recipient_id=1,
            channel=NotificationChannel.TELEGRAM,
            message_text="второй возврат",
        )
    )
    db.commit()
    count = db.scalar(
        select(m.Notification).where(
            m.Notification.prescription_id == prescription.id
        )
    )
    assert count is not None


def test_enums_stored_as_values(db, inspector) -> None:
    raw = db.execute(
        text("SELECT role FROM users WHERE id = :uid"), {"uid": inspector.id}
    ).scalar()
    assert raw == "inspector"


def test_norm_ref_suggested_vs_confirmed(db, prescription) -> None:
    ref1 = m.PrescriptionNormRef(
        prescription_id=prescription.id,
        doc_code="СП 63.13330.2018",
        doc_item="5.3",
        source=m.NormRefSource.RAG,
        status=m.NormRefStatus.SUGGESTED,
    )
    ref2 = m.PrescriptionNormRef(
        prescription_id=prescription.id,
        doc_code="СНиП 12-04-2002",
        doc_item=None,
        source=m.NormRefSource.EXPLICIT,
        status=m.NormRefStatus.CONFIRMED,
    )
    db.add_all([ref1, ref2])
    db.commit()
    db.refresh(prescription)
    assert [r.doc_code for r in prescription.suggested_norm_refs] == ["СП 63.13330.2018"]
    assert [r.doc_code for r in prescription.confirmed_norm_refs] == ["СНиП 12-04-2002"]


def test_default_notification_status_pending(db, prescription) -> None:
    n = m.Notification(
        prescription_id=prescription.id,
        event_type=NotificationEvent.DEADLINE_OVERDUE,
        event_version=1,
        recipient_type="user",
        recipient_id=1,
        message_text="просрочка",
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    assert n.status == NotificationStatus.PENDING
    assert n.attempt_count == 0


def test_sqlite_wal_and_foreign_keys() -> None:
    eng = get_engine()
    with eng.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert str(mode).lower() == "wal"
    assert int(fk) == 1
