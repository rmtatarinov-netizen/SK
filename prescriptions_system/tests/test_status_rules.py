"""Тесты статусной машины и сервиса предписаний (Шаг 4)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.db.models import (
    Notification,
    Prescription,
    PrescriptionHistory,
    PrescriptionStatus as PS,
    PrescriptionType as PT,
    UserRole,
)
from src.notifications.enums import NotificationEvent
from src.prescriptions.schemas import PrescriptionCreate
from src.prescriptions.service import (
    change_status,
    confirm_registration,
    create_prescription,
)
from src.prescriptions.status_rules import validate_status_transition


def _notifications(db, prescription, event) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification).where(
                Notification.prescription_id == prescription.id,
                Notification.event_type == event,
            )
        )
    )


# --- прямые проверки правил -------------------------------------------------


def test_illegal_transition_rejected(db, prescription, inspector):
    with pytest.raises(PermissionError):
        change_status(db, prescription, PS.FIXED_PENDING_REVIEW, inspector)


def test_same_status_rejected(db, prescription, inspector):
    with pytest.raises(ValueError):
        change_status(db, prescription, PS.NEW, inspector)


def test_admin_cannot_perform_review_transition(db, prescription, admin):
    with pytest.raises(PermissionError):
        change_status(db, prescription, PS.UNDER_REVIEW, admin)


def test_draft_to_new_requires_signed_scan(db, internal_prescription, inspector):
    with pytest.raises(ValueError):
        change_status(db, internal_prescription, PS.NEW, inspector)


# --- примеры из спецификации (Шаг 13) ---------------------------------------


def test_return_requires_comment(db, prescription, inspector):
    prescription.status = PS.FIXED_PENDING_REVIEW
    with pytest.raises(ValueError):
        change_status(db, prescription, PS.IN_WORK, inspector, comment=None)


def test_contractor_cannot_reject_prescription(db, prescription, contractor_user):
    prescription.status = PS.IN_WORK
    with pytest.raises(PermissionError):
        change_status(
            db, prescription, PS.REJECTED, contractor_user, comment="попытка"
        )


# --- реальные переходы, история и outbox ------------------------------------


def test_new_to_under_review_writes_history_and_outbox(
    db, prescription, inspector
):
    change_status(db, prescription, PS.UNDER_REVIEW, inspector)
    assert prescription.status == PS.UNDER_REVIEW
    hist = db.scalars(
        select(PrescriptionHistory).where(
            PrescriptionHistory.prescription_id == prescription.id,
            PrescriptionHistory.change_type == "status_changed",
        )
    ).all()
    assert len(hist) == 1
    notes = _notifications(
        db, prescription, NotificationEvent.STATUS_CHANGED_TO_UNDER_REVIEW
    )
    # до подтверждения инспектору; подрядчик не уведомляется (§0.4 по умолчанию)
    assert notes and all(n.recipient_id == inspector.id for n in notes)
    assert all(n.status.value == "pending" for n in notes)


def test_contractor_moves_own_prescription_to_in_work(db, prescription, contractor_user, inspector):
    change_status(db, prescription, PS.IN_WORK, contractor_user)
    assert prescription.status == PS.IN_WORK
    notes = _notifications(
        db, prescription, NotificationEvent.STATUS_CHANGED_TO_IN_WORK
    )
    assert any(n.recipient_id == inspector.id for n in notes)


def test_contractor_cannot_touch_foreign_prescription(
    db, prescription, obj, contractor, contractor_user
):
    from src.db.models import Contractor

    c2 = Contractor(name="ООО Чужой Подряд")
    db.add(c2)
    db.commit()
    foreign = Prescription(
        prescription_type=PT.CUSTOMER,
        doc_number="П-99/2026",
        doc_date=prescription.doc_date,
        object_id=obj.id,
        contractor_id=c2.id,
        description="чужой подрядчик",
        status=PS.NEW,
    )
    db.add(foreign)
    db.commit()
    with pytest.raises(PermissionError):
        change_status(db, foreign, PS.IN_WORK, contractor_user)


def test_reopen_closed_only_by_admin(db, prescription, inspector, admin):
    prescription.status = PS.CLOSED
    db.commit()
    with pytest.raises(PermissionError):
        change_status(db, prescription, PS.IN_WORK, inspector)
    change_status(db, prescription, PS.IN_WORK, admin)
    assert prescription.status == PS.IN_WORK


def test_event_version_increases_on_repeat_return(
    db, prescription, contractor_user, inspector
):
    change_status(db, prescription, PS.IN_WORK, contractor_user)
    change_status(db, prescription, PS.FIXED_PENDING_REVIEW, contractor_user)
    change_status(
        db, prescription, PS.IN_WORK, inspector, comment="недоделано 1"
    )
    change_status(db, prescription, PS.FIXED_PENDING_REVIEW, contractor_user)
    change_status(
        db, prescription, PS.IN_WORK, inspector, comment="недоделано 2"
    )
    returns = _notifications(
        db, prescription, NotificationEvent.RETURNED_TO_IN_WORK
    )
    versions = sorted(n.event_version for n in returns)
    assert versions == [1, 2]


# --- создание и подтверждение регистрации ------------------------------------


def test_create_requires_inspector_or_admin(db, contractor_user, obj, contractor):
    data = PrescriptionCreate(
        doc_number="X-1",
        doc_date="2026-01-01",
        object_id=obj.id,
        contractor_id=contractor.id,
        description="описание нарушения",
    )
    with pytest.raises(PermissionError):
        create_prescription(db, data, actor_user_id=contractor_user.id)


def test_create_internal_starts_as_draft(db, admin, obj, contractor):
    data = PrescriptionCreate(
        prescription_type=PT.INTERNAL,
        doc_number="INT-1",
        doc_date="2026-01-05",
        object_id=obj.id,
        contractor_id=contractor.id,
        description="описание",
        items=[{"text": "пункт 1"}],
    )
    p = create_prescription(db, data, actor_user_id=admin.id)
    assert p.status == PS.DRAFT_FOR_SIGNATURE
    assert p.requires_review is False
    assert len(p.items) == 1


def test_create_duplicate_rejected(db, admin, obj, contractor, prescription):
    data = PrescriptionCreate(
        prescription_type=PT.CUSTOMER,
        doc_number=prescription.doc_number,
        doc_date=prescription.doc_date,
        object_id=obj.id,
        contractor_id=contractor.id,
        description="дубль",
    )
    with pytest.raises(ValueError):
        create_prescription(db, data, actor_user_id=admin.id)


def test_confirm_registration_gates_contractor_notification(
    db, prescription, inspector, contractor_user
):
    # до подтверждения — уведомлений подрядчику нет
    notes = _notifications(db, prescription, NotificationEvent.PRESCRIPTION_CREATED)
    assert notes == []
    confirm_registration(db, prescription, actor_user_id=inspector.id)
    assert prescription.confirmed_at is not None
    assert prescription.requires_review is False
    notes = _notifications(db, prescription, NotificationEvent.PRESCRIPTION_CREATED)
    assert len(notes) >= 2  # инспектор + подрядчик
    recipients = {n.recipient_id for n in notes}
    assert inspector.id in recipients


def test_confirm_twice_rejected(db, prescription, inspector):
    confirm_registration(db, prescription, actor_user_id=inspector.id)
    with pytest.raises(ValueError):
        confirm_registration(db, prescription, actor_user_id=inspector.id)


def test_validate_direct_wrong_role(db, prescription, admin):
    with pytest.raises(PermissionError):
        validate_status_transition(
            old_status=PS.NEW,
            new_status=PS.UNDER_REVIEW,
            changed_by_user=admin,
            prescription=prescription,
        )
