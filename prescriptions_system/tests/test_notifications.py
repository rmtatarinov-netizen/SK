"""Тесты доставки уведомлений (Шаг 8): outbox, роутер, бэккофф, MAX-заглушка."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from src.db.models import Notification, NotificationAttempt, RecipientChannel
from src.notifications import router as router_module
from src.notifications import worker
from src.notifications.adapters.base import AdapterError
from src.notifications.adapters.max import MaxAdapter
from src.notifications.enums import (
    NotificationChannel,
    NotificationEvent,
    NotificationStatus,
)
from src.notifications.outbox import claim_due_notifications
from src.notifications.service import create_notification_outbox_records
from src.notifications.templates import render_message
from tests.fakes.adapters import FakeAdapter

BASE = datetime(2026, 10, 3, 12, 0, 0)

S = NotificationStatus
E = NotificationEvent
C = NotificationChannel


def _add_channel(
    db, user_id: int, channel: NotificationChannel, address: str, preferred: bool = False
) -> RecipientChannel:
    row = RecipientChannel(
        recipient_type="user",
        recipient_id=user_id,
        channel=channel,
        address=address,
        is_preferred=preferred,
    )
    db.add(row)
    db.commit()
    return row


def _pending_notification(db, prescription, recipient_id: int) -> Notification:
    n = Notification(
        prescription_id=prescription.id,
        event_type=E.PRESCRIPTION_CREATED,
        event_version=1,
        recipient_type="user",
        recipient_id=recipient_id,
        message_text="тестовое сообщение",
        status=S.PENDING,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


@pytest.fixture()
def adapters(monkeypatch):
    """Реестр фейковых адаптеров: channel -> FakeAdapter. Патчит router.get_adapter."""
    registry: dict[NotificationChannel, FakeAdapter] = {}

    def fake_get_adapter(channel: NotificationChannel) -> FakeAdapter:
        if channel not in registry:
            registry[channel] = FakeAdapter()
        return registry[channel]

    monkeypatch.setattr(router_module, "get_adapter", fake_get_adapter)
    return registry


def test_successful_send(db, prescription, inspector, adapters):
    _add_channel(db, inspector.id, C.TELEGRAM, "11111")
    n = _pending_notification(db, prescription, inspector.id)

    counters = worker.process_pending_notifications(
        db, adapter_provider=lambda ch: adapters[ch], now=BASE
    )
    db.refresh(n)
    assert counters["sent"] == 1
    assert n.status == S.SENT
    assert n.channel == C.TELEGRAM
    assert n.external_message_id == "fake-msg-1"
    assert n.sent_at is not None
    attempts = db.scalars(
        select(NotificationAttempt).where(
            NotificationAttempt.notification_id == n.id
        )
    ).all()
    assert len(attempts) == 1
    assert attempts[0].success is True


def test_backoff_delays_and_recovery(db, prescription, inspector, adapters):
    _add_channel(db, inspector.id, C.TELEGRAM, "22222")
    n = _pending_notification(db, prescription, inspector.id)
    fake = FakeAdapter(outcomes=["telegram: network error", "telegram: HTTP 500"])
    adapters[C.TELEGRAM] = fake

    counters = worker.process_pending_notifications(
        db, adapter_provider=lambda ch: adapters[ch], now=BASE
    )
    db.refresh(n)
    assert counters["retry"] == 1
    assert n.status == S.PENDING
    assert n.attempt_count == 1
    assert n.next_attempt_at == BASE + timedelta(seconds=60)

    t2 = BASE + timedelta(seconds=61)
    counters = worker.process_pending_notifications(
        db, adapter_provider=lambda ch: adapters[ch], now=t2
    )
    db.refresh(n)
    assert n.attempt_count == 2
    assert n.next_attempt_at == t2 + timedelta(seconds=300)

    t3 = t2 + timedelta(seconds=301)
    counters = worker.process_pending_notifications(
        db, adapter_provider=lambda ch: adapters[ch], now=t3
    )
    db.refresh(n)
    assert counters["sent"] == 1
    assert n.status == S.SENT
    assert n.attempt_count == 3


def test_permanent_failure_keeps_record(db, prescription, inspector, adapters):
    _add_channel(db, inspector.id, C.TELEGRAM, "33333")
    n = _pending_notification(db, prescription, inspector.id)
    fake = FakeAdapter(outcomes=["boom"] * 10)
    adapters[C.TELEGRAM] = fake

    now = BASE
    for expected_attempt in range(1, 5):
        counters = worker.process_pending_notifications(
            db, adapter_provider=lambda ch: adapters[ch], now=now
        )
        db.refresh(n)
        assert n.attempt_count == expected_attempt
        assert n.status == S.PENDING
        now = n.next_attempt_at + timedelta(seconds=1)

    counters = worker.process_pending_notifications(
        db, adapter_provider=lambda ch: adapters[ch], now=now
    )
    db.refresh(n)
    assert counters["failed"] == 1
    assert n.status == S.FAILED
    assert n.attempt_count == 5
    assert n.last_error == "boom"
    assert n.next_attempt_at is None
    # запись не удалена
    assert db.get(Notification, n.id) is not None
    fails = db.scalars(
        select(NotificationAttempt).where(
            NotificationAttempt.notification_id == n.id,
            NotificationAttempt.success.is_(False),
        )
    ).all()
    assert len(fails) == 5


def test_no_channel_then_fixed_contact(db, prescription, inspector, adapters):
    n = _pending_notification(db, prescription, inspector.id)

    counters = worker.process_pending_notifications(
        db, adapter_provider=lambda ch: adapters[ch], now=BASE
    )
    db.refresh(n)
    assert counters["no_channel"] == 1
    assert n.status == S.NO_CHANNEL

    _add_channel(db, inspector.id, C.TELEGRAM, "44444")
    t2 = n.next_attempt_at + timedelta(seconds=1)
    counters = worker.process_pending_notifications(
        db, adapter_provider=lambda ch: adapters[ch], now=t2
    )
    db.refresh(n)
    assert n.status == S.SENT


def test_unavailable_adapter_gives_no_channel(db, prescription, inspector, adapters):
    _add_channel(db, inspector.id, C.TELEGRAM, "55555")
    adapters[C.TELEGRAM] = FakeAdapter(available=False)
    n = _pending_notification(db, prescription, inspector.id)

    counters = worker.process_pending_notifications(
        db, adapter_provider=lambda ch: adapters[ch], now=BASE
    )
    db.refresh(n)
    assert n.status == S.NO_CHANNEL


def test_router_prefers_preferred_available_channel(db, prescription, inspector, adapters):
    adapters[C.TELEGRAM] = FakeAdapter(available=True)
    adapters[C.MAX] = FakeAdapter(available=False)
    _add_channel(db, inspector.id, C.MAX, "max-1", preferred=True)
    _add_channel(db, inspector.id, C.TELEGRAM, "tg-1")

    n = _pending_notification(db, prescription, inspector.id)
    resolved = router_module.resolve_channel(db, n)
    assert resolved == (C.TELEGRAM, "tg-1")


def test_max_adapter_raises_not_implemented():
    adapter = MaxAdapter()
    assert adapter.is_available() is False
    with pytest.raises(NotImplementedError):
        adapter.send("text", "max-user")
    with pytest.raises(NotImplementedError):
        adapter.send_file("max-user", "file.pdf")


def test_outbox_idempotent_repeated_event(db, prescription, inspector):
    created1 = create_notification_outbox_records(
        db, prescription=prescription, event=E.PRESCRIPTION_CREATED
    )
    db.commit()
    # повторная обработка одного и того же события не плодит дубли
    created2 = create_notification_outbox_records(
        db, prescription=prescription, event=E.PRESCRIPTION_CREATED
    )
    db.commit()
    rows = db.scalars(
        select(Notification).where(
            Notification.prescription_id == prescription.id,
            Notification.event_type == E.PRESCRIPTION_CREATED,
        )
    ).all()
    assert created1 >= 1
    assert created2 == 0
    assert len(rows) == created1


def test_claim_only_due(db, prescription, inspector, adapters):
    n = _pending_notification(db, prescription, inspector.id)
    n.next_attempt_at = BASE + timedelta(hours=1)
    db.commit()
    assert claim_due_notifications(db, now=BASE) == []
    assert len(claim_due_notifications(db, now=BASE + timedelta(hours=2))) == 1


def test_message_template_contains_required_fields(db, prescription, inspector):
    prescription.responsible_user_id = None
    text = render_message(E.PRESCRIPTION_CREATED, prescription)
    assert prescription.doc_number in text
    assert prescription.object.name in text
    assert prescription.contractor.name in text
    assert prescription.deadline.isoformat() in text
    assert f"/prescriptions/{prescription.id}" in text
