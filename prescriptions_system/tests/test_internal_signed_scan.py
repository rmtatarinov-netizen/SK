"""Тесты внутреннего подписанного скана (Шаг 4): приём, дедупликация, сверка."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.db.models import (
    AttachmentKind,
    Notification,
    Prescription,
    PrescriptionAttachment,
    PrescriptionHistory,
    PrescriptionStatus as PS,
)
from src.notifications.enums import NotificationEvent
from src.prescriptions.print_service import generate_print_version
from src.prescriptions.service import change_status
from src.prescriptions.signed_scan_service import (
    link_signed_scan,
    verify_signed_scan,
)


@pytest.fixture()
def scan_file(tmp_path):
    path = tmp_path / "scan1.pdf"
    path.write_bytes(b"%PDF-1.4 signed scan bytes")
    return path


@pytest.fixture()
def with_print_version(db, internal_prescription, admin, inspector, tmp_storage):
    generate_print_version(db, internal_prescription, actor_user_id=admin.id)
    return internal_prescription


def _events(db, prescription, event):
    return list(
        db.scalars(
            select(Notification).where(
                Notification.prescription_id == prescription.id,
                Notification.event_type == event,
            )
        )
    )


def test_link_requires_print_version(db, internal_prescription, admin, scan_file):
    with pytest.raises(ValueError):
        link_signed_scan(
            db, internal_prescription, scan_file, actor_user_id=admin.id
        )


def test_link_scan_after_print(db, with_print_version, admin, scan_file):
    attachment = link_signed_scan(
        db, with_print_version, scan_file, actor_user_id=admin.id
    )
    assert attachment.kind == AttachmentKind.SCAN
    assert attachment.print_version_id is not None
    assert with_print_version.signed_scan_attachment_id == attachment.id
    assert with_print_version.signed_scan_verified_at is None
    assert _events(
        db, with_print_version, NotificationEvent.INTERNAL_SIGNED_SCAN_UPLOADED
    )


def test_duplicate_scan_no_double(db, with_print_version, admin, scan_file):
    a1 = link_signed_scan(db, with_print_version, scan_file, actor_user_id=admin.id)
    a2 = link_signed_scan(db, with_print_version, scan_file, actor_user_id=admin.id)
    assert a1.id == a2.id
    count = db.scalar(
        select(PrescriptionAttachment.id).where(
            PrescriptionAttachment.prescription_id == with_print_version.id
        )
    )
    assert count is not None
    total = len(
        db.scalars(
            select(PrescriptionAttachment.id).where(
                PrescriptionAttachment.prescription_id == with_print_version.id
            )
        ).all()
    )
    assert total == 1


def test_contractor_cannot_link_or_verify(db, with_print_version, contractor_user, scan_file):
    with pytest.raises(PermissionError):
        link_signed_scan(
            db, with_print_version, scan_file, actor_user_id=contractor_user.id
        )
    with pytest.raises(PermissionError):
        verify_signed_scan(db, with_print_version, actor_user_id=contractor_user.id)


def test_scan_for_customer_rejected(db, prescription, admin, scan_file, tmp_storage):
    with pytest.raises(ValueError):
        link_signed_scan(db, prescription, scan_file, actor_user_id=admin.id)


def test_draft_to_new_blocked_until_verify(db, with_print_version, inspector, admin, scan_file):
    with pytest.raises(ValueError):
        change_status(db, with_print_version, PS.NEW, inspector)

    link_signed_scan(db, with_print_version, scan_file, actor_user_id=admin.id)
    verify_signed_scan(db, with_print_version, actor_user_id=inspector.id)
    assert with_print_version.signed_scan_verified_at is not None
    assert _events(
        db, with_print_version, NotificationEvent.INTERNAL_SIGNED_SCAN_VERIFIED
    )

    p = change_status(db, with_print_version, PS.NEW, inspector)
    assert p.status == PS.NEW
    assert p.confirmed_at is not None and p.confirmed_by_user_id == inspector.id
    created = _events(db, p, NotificationEvent.PRESCRIPTION_CREATED)
    assert created


def test_verify_without_scan_fails(db, with_print_version, inspector):
    with pytest.raises(ValueError):
        verify_signed_scan(db, with_print_version, actor_user_id=inspector.id)


def test_link_history_recorded(db, with_print_version, admin, scan_file):
    link_signed_scan(db, with_print_version, scan_file, actor_user_id=admin.id)
    history = db.scalars(
        select(PrescriptionHistory).where(
            PrescriptionHistory.prescription_id == with_print_version.id,
            PrescriptionHistory.change_type == "signed_scan_attached",
        )
    ).all()
    assert len(history) == 1
