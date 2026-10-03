"""Тесты Telegram-бота (Шаг 10): команды, клавиатура из правил, фото, callback.

Транспорт — FakeBot; сеть не используется. Бот только вызывает сервисы и
статусную машину, бизнес-правила не дублирует.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from src.bots.handlers import Update, allowed_actions, card_text, process_update
from src.db import models as m
from src.db.models import (
    Notification,
    Prescription,
    PrescriptionAttachment,
    PrescriptionHistory,
    PrescriptionStatus as PS,
    RecipientChannel,
    User,
    UserRole,
)
from src.notifications.enums import NotificationChannel, NotificationEvent
from src.prescriptions.service import change_status, confirm_registration
from tests.fakes.bot import FakeBot

INSPECTOR_CHAT = 111
CONTRACTOR_CHAT = 222


def _link(db, user, chat_id):
    db.add(
        RecipientChannel(
            recipient_type="user",
            recipient_id=user.id,
            channel=NotificationChannel.TELEGRAM,
            address=str(chat_id),
        )
    )
    db.commit()

@pytest.fixture()
def bot():
    return FakeBot()

@pytest.fixture()
def linked(db, inspector, contractor_user):
    _link(db, inspector, INSPECTOR_CHAT)
    _link(db, contractor_user, CONTRACTOR_CHAT)


def _msg(bot, db, chat_id, text):
    process_update(
        db,
        Update(chat_id=chat_id, text=text),
        send=bot.send,
        answer_callback=bot.answer,
        fetch_photo=bot.fetch_photo,
    )


def _to(db, p, status, user):
    return change_status(db, p, status, user)


# --- привязка / help ---------------------------------------------------------


def test_unlinked_chat_is_rejected(db, bot):
    _msg(bot, db, 999, "/start")
    assert "не привязан" in bot.last_text.lower()


def test_start_help_depends_on_role(db, bot, linked, inspector, contractor_user):
    _msg(bot, db, INSPECTOR_CHAT, "/start")
    assert "/close" in bot.last_text
    _msg(bot, db, CONTRACTOR_CHAT, "/start")
    assert "/my_prescriptions" in bot.last_text


# --- списки / карточка -------------------------------------------------------


def test_my_prescriptions_contractor_only_own(
    db, bot, linked, contractor_user, contractor, obj
):
    foreign = Prescription(
        prescription_type=m.PrescriptionType.CUSTOMER,
        doc_number="CHUZHOE-1/2026",
        doc_date=date(2026, 1, 1),
        object_id=obj.id,
        contractor_id=9999,
        description="чужое предписание",
        status=PS.NEW,
    )
    db.add(foreign)
    db.commit()
    _msg(bot, db, CONTRACTOR_CHAT, "/my_prescriptions")
    assert "CHUZHOE-1/2026" not in bot.last_text


def test_open_card_keyboard_contractor(
    db, bot, linked, contractor_user, prescription
):
    _msg(bot, db, CONTRACTOR_CHAT, "/open " + prescription.doc_number)
    assert prescription.doc_number in bot.last_text
    kb = bot.last_keyboard
    callbacks = [
        cell["callback_data"] for row in kb["inline_keyboard"] for cell in row
    ]
    assert any("in_work" in cd for cd in callbacks)
    assert not any("close" in cd for cd in callbacks)


def test_card_requires_review_marker(
    db, bot, linked, contractor_user, prescription
):
    assert prescription.requires_review is True
    _msg(bot, db, CONTRACTOR_CHAT, "/open " + prescription.doc_number)
    assert "не проверены" in bot.last_text


# --- статусные команды -------------------------------------------------------


def test_in_work_by_contractor_notifies_inspector(
    db, bot, linked, contractor_user, inspector, prescription
):
    _msg(bot, db, CONTRACTOR_CHAT, "/in_work " + prescription.doc_number)
    db.refresh(prescription)
    assert prescription.status == PS.IN_WORK
    assert "Готово" in bot.last_text
    notes = db.scalars(
        select(Notification).where(
            Notification.prescription_id == prescription.id,
            Notification.event_type == NotificationEvent.STATUS_CHANGED_TO_IN_WORK,
        )
    ).all()
    assert notes and {n.recipient_id for n in notes} == {inspector.id}


def test_in_work_foreign_contractor_rejected(db, bot, contractor, obj):
    intruder = User(
        login="intruder",
        full_name="Чужой Подрядчик",
        role=UserRole.CONTRACTOR_RESPONSIBLE,
        password_hash="x",
        contractor_id=9999,
    )
    db.add(intruder)
    db.commit()
    _link(db, intruder, CONTRACTOR_CHAT)
    p = Prescription(
        prescription_type=m.PrescriptionType.CUSTOMER,
        doc_number="PRIV-1/2026",
        doc_date=date(2026, 2, 2),
        object_id=obj.id,
        contractor_id=contractor.id,
        description="принадлежит contractor, но не intruder",
        status=PS.NEW,
    )
    db.add(p)
    db.commit()
    _msg(bot, db, CONTRACTOR_CHAT, "/in_work " + p.doc_number)
    assert "Ошибка" in bot.last_text


def test_fixed_then_rework_requires_comment(
    db, bot, linked, contractor_user, inspector, prescription
):
    _to(db, prescription, PS.IN_WORK, contractor_user)
    _msg(bot, db, CONTRACTOR_CHAT, "/fixed " + prescription.doc_number)
    db.refresh(prescription)
    assert prescription.status == PS.FIXED_PENDING_REVIEW

    _msg(bot, db, INSPECTOR_CHAT, "/rework " + prescription.doc_number)
    assert "комментарий" in bot.last_text.lower()

    _msg(
        bot,
        db,
        INSPECTOR_CHAT,
        "/rework " + prescription.doc_number + " недоделано",
    )
    db.refresh(prescription)
    assert prescription.status == PS.IN_WORK


def test_close_after_confirm_notifies_contractor(
    db, bot, linked, contractor_user, inspector, prescription
):
    confirm_registration(db, prescription, actor_user_id=inspector.id)
    _to(db, prescription, PS.IN_WORK, contractor_user)
    _to(db, prescription, PS.FIXED_PENDING_REVIEW, contractor_user)
    _msg(
        bot,
        db,
        INSPECTOR_CHAT,
        "/close " + prescription.doc_number + " подтверждено",
    )
    db.refresh(prescription)
    assert prescription.status == PS.CLOSED
    assert prescription.closed_at is not None
    assert prescription.closed_by_user_id == inspector.id
    closed = db.scalars(
        select(Notification).where(
            Notification.prescription_id == prescription.id,
            Notification.event_type == NotificationEvent.PRESCRIPTION_CLOSED,
        )
    ).all()
    assert {n.recipient_id for n in closed} >= {inspector.id, contractor_user.id}


# --- comment -----------------------------------------------------------------


def test_comment_history_added(db, bot, linked, contractor_user, prescription):
    _msg(
        bot,
        db,
        CONTRACTOR_CHAT,
        "/comment " + prescription.doc_number + " работы идут",
    )
    entries = db.scalars(
        select(PrescriptionHistory).where(
            PrescriptionHistory.prescription_id == prescription.id,
            PrescriptionHistory.change_type == "comment_added",
        )
    ).all()
    assert len(entries) == 1
    assert entries[0].comment == "работы идут"


# --- callbacks ---------------------------------------------------------------


def test_callback_button_in_work(db, bot, linked, contractor_user, prescription):
    process_update(
        db,
        Update(
            chat_id=CONTRACTOR_CHAT,
            kind="callback",
            callback_id="cb1",
            callback_data=f"act:{prescription.id}:in_work",
        ),
        send=bot.send,
        answer_callback=bot.answer,
        fetch_photo=bot.fetch_photo,
    )
    db.refresh(prescription)
    assert prescription.status == PS.IN_WORK
    assert bot.answers and "Готово" in bot.answers[-1][1]


def test_callback_unknown_button(db, bot, linked, contractor_user):
    process_update(
        db,
        Update(
            chat_id=CONTRACTOR_CHAT,
            kind="callback",
            callback_id="cb2",
            callback_data="act:9999:bogus",
        ),
        send=bot.send,
        answer_callback=bot.answer,
        fetch_photo=bot.fetch_photo,
    )
    assert bot.answers


# --- фото --------------------------------------------------------------------


def _photo(bot, db, chat_id, caption):
    process_update(
        db,
        Update(chat_id=chat_id, text=caption, photo_file_ids=["f1"]),
        send=bot.send,
        answer_callback=bot.answer,
        fetch_photo=bot.fetch_photo,
    )


def test_photo_in_work_creates_attachment_once(
    db, bot, linked, contractor_user, prescription
):
    _to(db, prescription, PS.IN_WORK, contractor_user)
    _photo(bot, db, CONTRACTOR_CHAT, f"{prescription.doc_number} устранено")
    assert "Фото добавлено" in bot.last_text
    atts = db.scalars(
        select(PrescriptionAttachment).where(
            PrescriptionAttachment.prescription_id == prescription.id
        )
    ).all()
    assert len(atts) == 1

    _photo(bot, db, CONTRACTOR_CHAT, prescription.doc_number)
    atts = db.scalars(
        select(PrescriptionAttachment).where(
            PrescriptionAttachment.prescription_id == prescription.id
        )
    ).all()
    assert len(atts) == 1


def test_photo_wrong_status_rejected(
    db, bot, linked, contractor_user, prescription
):
    assert prescription.status == PS.NEW
    _photo(bot, db, CONTRACTOR_CHAT, prescription.doc_number)
    assert "Ошибка" in bot.last_text
    assert db.query(PrescriptionAttachment).count() == 0


def test_photo_without_caption(db, bot, linked, contractor_user):
    process_update(
        db,
        Update(chat_id=CONTRACTOR_CHAT, text=None, photo_file_ids=["f1"]),
        send=bot.send,
        answer_callback=bot.answer,
        fetch_photo=bot.fetch_photo,
    )
    assert "подписью" in bot.last_text.lower()


# --- прямые проверки helpers -------------------------------------------------


def test_allowed_actions_new_inspector(db, linked, inspector, prescription):
    assert set(allowed_actions(prescription, inspector)) == {"in_work", "reject"}


def test_card_text_fields(db, linked, contractor_user, inspector, prescription):
    text = card_text(prescription)
    assert prescription.doc_number in text
    assert prescription.object.name in text
    assert prescription.contractor.name in text
