"""Тесты печатной версии внутреннего предписания (Шаг 4)."""

from __future__ import annotations

import hashlib
from datetime import date

import fitz
import pytest
from sqlalchemy import select

from src.db.models import (
    Notification,
    PrescriptionHistory,
    PrescriptionType as PT,
)
from src.notifications.enums import NotificationEvent
from src.prescriptions.print_service import (
    generate_print_version,
    request_print,
)
from src.prescriptions.schemas import PrescriptionCreate
from src.prescriptions.service import create_prescription


@pytest.fixture()
def internal(db, admin, obj, contractor):
    data = PrescriptionCreate(
        prescription_type=PT.INTERNAL,
        doc_number="INT-7/2026",
        doc_date=date(2026, 9, 5),
        object_id=obj.id,
        contractor_id=contractor.id,
        description="Missing guardrail at level difference.",
        deadline=date(2026, 9, 20),
        items=[{"text": "Install guardrail"}, {"text": "Check anchors"}],
    )
    return create_prescription(db, data, actor_user_id=admin.id)


def _events(db, prescription, event):
    return list(
        db.scalars(
            select(Notification).where(
                Notification.prescription_id == prescription.id,
                Notification.event_type == event,
            )
        )
    )


def test_generate_creates_valid_pdf(db, internal, admin, tmp_storage):
    version = generate_print_version(db, internal, actor_user_id=admin.id)
    assert version.version_number == 1
    path = tmp_storage / "storage" / "print_versions" / f"prescription_{internal.id}" / "v1.pdf"
    assert path.exists()
    doc = fitz.open(str(path))
    assert doc.page_count == 1
    text = doc[0].get_text()
    doc.close()
    assert "INT-7/2026" in text
    assert hashlib.sha256(path.read_bytes()).hexdigest() == version.file_hash


def test_versions_immutable_and_increment(db, internal, admin, tmp_storage):
    v1 = generate_print_version(db, internal, actor_user_id=admin.id)
    bytes_v1 = (tmp_storage / "storage" / "print_versions" / f"prescription_{internal.id}" / "v1.pdf").read_bytes()
    v2 = generate_print_version(db, internal, actor_user_id=admin.id)
    assert v2.version_number == 2
    assert v1.file_hash != v2.file_hash or v1.id != v2.id
    stored_v1 = (tmp_storage / "storage" / "print_versions" / f"prescription_{internal.id}" / "v1.pdf").read_bytes()
    assert stored_v1 == bytes_v1  # первая версия не перезаписана


def test_print_forbidden_for_customer(db, prescription, admin, tmp_storage):
    with pytest.raises(ValueError):
        generate_print_version(db, prescription, actor_user_id=admin.id)


def test_contractor_cannot_generate_or_print(db, internal, contractor_user, tmp_storage):
    with pytest.raises(PermissionError):
        generate_print_version(db, internal, actor_user_id=contractor_user.id)
    with pytest.raises(PermissionError):
        request_print(db, internal, actor_user_id=contractor_user.id)


def test_print_event_outbox_inspector_only(db, internal, admin, inspector, tmp_storage):
    generate_print_version(db, internal, actor_user_id=admin.id)
    events = _events(
        db, internal, NotificationEvent.INTERNAL_PRINT_VERSION_GENERATED
    )
    assert events
    assert all(n.recipient_id == inspector.id for n in events)


def test_request_print_writes_audit(db, internal, inspector, tmp_storage):
    generate_print_version(db, internal, actor_user_id=inspector.id)
    version = request_print(db, internal, actor_user_id=inspector.id)
    assert version.version_number == 1
    audit = db.scalars(
        select(PrescriptionHistory).where(
            PrescriptionHistory.prescription_id == internal.id,
            PrescriptionHistory.change_type == "print_requested",
        )
    ).all()
    assert len(audit) == 1


def test_request_print_without_version_fails(db, internal, inspector, tmp_storage):
    with pytest.raises(ValueError):
        request_print(db, internal, actor_user_id=inspector.id)
