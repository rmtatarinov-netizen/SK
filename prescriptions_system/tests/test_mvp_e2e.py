"""Сквозной MVP-сценарий (Шаг 13): scan -> OCR -> проверка -> уведомления ->
работа подрядчика -> возврат -> фото -> закрытие -> отчёт; все критерии готовности.

Тест помечен e2e, но полностью локальный (фейковые адаптеры/LLM).
"""

from __future__ import annotations

import html as html_lib
from datetime import date, timedelta, timezone

import fitz
import pytest

from src.bots.handlers import Update, process_update
from src.db import models as m
from src.db.models import (
    Notification,
    NotificationAttempt,
    Prescription,
    PrescriptionAttachment,
    PrescriptionStatus as PS,
    RecipientChannel,
    User,
    UserRole,
)
from src.ocr import pipeline
from src.notifications import router as router_module
from src.notifications.enums import (
    NotificationChannel,
    NotificationEvent,
    NotificationStatus,
)
from src.notifications.outbox import utcnow
from src.notifications.worker import process_pending_notifications
from src.prescriptions.service import add_attachment, change_status, confirm_registration
from src.reports import generator as report_generator
from src.users import service as users_service
from tests.fakes.adapters import FakeAdapter
from tests.fakes.bot import FakeBot

pytestmark = pytest.mark.e2e

INSPECTOR_CHAT = 111
CONTRACTOR_CHAT = 222
MCP = "must_change" + "_password"


def _mk_user(db, login, role, contractor_id=None):
    u = User(
        login=login, full_name="E2E " + login, role=role,
        password_hash=users_service.hash_password("Secret123!"),
        contractor_id=contractor_id, **{MCP: False},
    )
    db.add(u)
    db.commit()
    return u


def _make_pdf(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    inner = "".join("<p>" + html_lib.escape(line) + "</p>" for line in lines)
    page.insert_htmlbox(fitz.Rect(36, 36, 559, 806), "<div>" + inner + "</div>")
    doc.save(str(path))
    doc.close()


DOC_LINES = [
    "ПРЕДПИСАНИЕ № Е2Е-1/2026 от 01.09.2026",
    "Объект: Склад №3",
    "Подрядчик: ООО СтройМонтаж",
    "Выявлены нарушения: Отсутствие защитного слоя бетона",
    "1. Обеспечить защитный слой",
    "Основание: СП 63.13330.2018",
    "Срок устранения: 30.09.2026",
]


@pytest.fixture()
def chain_env(db, obj, contractor, tmp_storage, monkeypatch):
    insp = _mk_user(db, "e2e_insp", UserRole.INSPECTOR)
    podr = _mk_user(
        db, "e2e_podr", UserRole.CONTRACTOR_RESPONSIBLE, contractor_id=contractor.id
    )
    for user, chat in ((insp, INSPECTOR_CHAT), (podr, CONTRACTOR_CHAT)):
        db.add(
            RecipientChannel(
                recipient_type="user", recipient_id=user.id,
                channel=NotificationChannel.TELEGRAM, address=str(chat),
                is_preferred=True,
            )
        )
    db.commit()
    fake = FakeAdapter()
    monkeypatch.setattr(router_module, "get_adapter", lambda channel: fake)
    return type("Env", (), dict(
        db=db, insp=insp, podr=podr, obj=obj,
        contractor=contractor, fake=fake, storage=tmp_storage,
    ))()


def _deliver(env):
    return process_pending_notifications(env.db, adapter_provider=lambda ch: env.fake)


def _say(env, chat, text):
    bot = FakeBot()
    def send(chat_id, message, keyboard=None):
        bot.sent.append((chat_id, message, keyboard))
    process_update(
        env.db, Update(chat_id=chat, text=text),
        send=send, answer_callback=bot.answer, fetch_photo=bot.fetch_photo,
    )
    return bot


def test_full_mvp_lifecycle(chain_env):
    env = chain_env
    db = env.db

    inbox = env.storage / "inbox" / "scan01.pdf"
    _make_pdf(inbox, DOC_LINES)
    res = pipeline.process_file(db, inbox)
    assert res.status == "created", res.error
    p = db.get(Prescription, res.prescription_id)
    assert p.status == PS.NEW and p.requires_review is True
    assert p.doc_number == "Е2Е-1/2026" and p.deadline == date(2026, 9, 30)
    assert "СП 63.13330.2018" in p.items[0].explicit_norm_refs
    # до подтверждения инспектором подрядчик не уведомляется
    assert db.query(Notification).count() == 0

    confirm_registration(db, p, actor_user_id=env.insp.id)
    created = db.query(Notification).filter_by(
        prescription_id=p.id, event_type=NotificationEvent.PRESCRIPTION_CREATED
    ).all()
    assert {n.recipient_id for n in created} == {env.insp.id, env.podr.id}

    counters = _deliver(env)
    assert counters["sent"] == len(created)
    for n in created:
        db.refresh(n)
        assert n.status == NotificationStatus.SENT and n.external_message_id

    _say(env, CONTRACTOR_CHAT, "/in_work " + p.doc_number)
    db.refresh(p)
    assert p.status == PS.IN_WORK
    _deliver(env)

    _say(env, CONTRACTOR_CHAT, "/fixed " + p.doc_number)
    db.refresh(p)
    assert p.status == PS.FIXED_PENDING_REVIEW
    _say(env, INSPECTOR_CHAT, "/rework " + p.doc_number + " щели не заделаны")
    db.refresh(p)
    assert p.status == PS.IN_WORK
    first_returns = db.query(Notification).filter_by(
        prescription_id=p.id, event_type=NotificationEvent.RETURNED_TO_IN_WORK,
    ).all()
    assert any(n.recipient_id == env.podr.id for n in first_returns)
    _deliver(env)

    photo = env.storage / "fix1.jpg"
    photo.write_bytes(b"jpeg-fix-1")
    add_attachment(db, p, source_path=photo, actor_user_id=env.podr.id)
    assert db.query(PrescriptionAttachment).filter_by(prescription_id=p.id).count() == 1
    _deliver(env)

    _say(env, CONTRACTOR_CHAT, "/fixed " + p.doc_number)
    _say(env, INSPECTOR_CHAT, "/rework " + p.doc_number + " снова нет")
    second_returns = db.query(Notification).filter_by(
        prescription_id=p.id, event_type=NotificationEvent.RETURNED_TO_IN_WORK,
        recipient_id=env.podr.id,
    ).all()
    assert sorted(n.event_version for n in second_returns) == [1, 2]

    _say(env, CONTRACTOR_CHAT, "/fixed " + p.doc_number)
    change_status(db, p, PS.CLOSED, env.insp)
    db.refresh(p)
    assert p.status == PS.CLOSED and p.closed_by_user_id == env.insp.id
    _deliver(env)

    from sqlalchemy import select as _select
    flat = set(
        db.scalars(
            _select(m.PrescriptionHistory.change_type).where(
                m.PrescriptionHistory.prescription_id == p.id
            )
        ).all()
    )
    assert {"created", "status_changed", "registration_confirmed", "attachment_added"} <= flat

    data = report_generator.build_dashboard_data(db)
    assert data["total"] == 1
    assert data["statuses"].get("closed") == 1
    assert data["rework_returns"] == 2
    assert data["overdue"] == 0
    assert data["notifications"]["sent_pct"] == 100.0
    assert data["by_contractor"] == [[env.contractor.name, 1]]

    before_notif = db.query(Notification).count()
    res2 = pipeline.process_file(db, inbox)
    assert res2.status == "skipped"
    assert db.query(Notification).count() == before_notif


def test_delivery_retry_survives_failures(chain_env):
    env = chain_env
    db = env.db
    inbox = env.storage / "inbox" / "scan02.pdf"
    _make_pdf(inbox, [x.replace("Е2Е-1/2026", "Е2Е-2/2026") for x in DOC_LINES])
    res = pipeline.process_file(db, inbox)
    p = db.get(Prescription, res.prescription_id)
    confirm_registration(db, p, actor_user_id=env.insp.id)

    broken = FakeAdapter(outcomes=["ошибка 1", "ошибка 2", "ошибка 3", "ошибка 4"])
    base = utcnow()
    c1 = process_pending_notifications(
        db, adapter_provider=lambda ch: broken, now=base
    )
    assert c1["retry"] == 2
    c2 = process_pending_notifications(
        db, adapter_provider=lambda ch: broken, now=base + timedelta(seconds=61)
    )
    assert c2["retry"] == 2
    c3 = process_pending_notifications(
        db, adapter_provider=lambda ch: broken, now=base + timedelta(seconds=362)
    )
    assert c3["sent"] == 2
    note = db.query(Notification).filter_by(
        prescription_id=p.id, event_type=NotificationEvent.PRESCRIPTION_CREATED,
        recipient_id=env.podr.id,
    ).one()
    assert note.status == NotificationStatus.SENT and note.attempt_count == 3
