"""Тесты адаптеров каналов и заглушек (Шаги 8/14). Сеть не используется."""

from __future__ import annotations

import pytest

from src.bots.max_bot import MaxBotStub
from src.db.models import Notification, RecipientChannel
from src.llm.stt import StubTranscriber, Transcriber, transcribe
from src.notifications.adapters.base import AdapterError, NotificationAdapter
from src.notifications.adapters.max import MaxAdapter
from src.notifications.adapters.telegram import TelegramAdapter
from src.notifications.enums import NotificationChannel, NotificationEvent, NotificationStatus
from src.notifications.router import candidate_channels, resolve_channel


def _make_pending(db, prescription, inspector):
    row = Notification(
        prescription_id=prescription.id,
        event_type=NotificationEvent.DEADLINE_OVERDUE,
        event_version=1,
        recipient_type="user",
        recipient_id=inspector.id,
        message_text="просрочка",
        status=NotificationStatus.PENDING,
    )
    db.add(row)
    db.commit()
    return row


def _add_channel(db, user_id, channel, address):
    db.add(
        RecipientChannel(
            recipient_type="user",
            recipient_id=user_id,
            channel=channel,
            address=address,
        )
    )
    db.commit()


# --- Telegram ---------------------------------------------------------------


def test_telegram_without_token_unavailable():
    adapter = TelegramAdapter(bot_token="")
    assert adapter.is_available() is False
    with pytest.raises(AdapterError):
        adapter.send("текст", "123")


def test_telegram_matches_protocol():
    assert isinstance(TelegramAdapter(bot_token="x"), NotificationAdapter)


def test_telegram_send_ok(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 987}}

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return Resp()

    monkeypatch.setattr(
        "src.notifications.adapters.telegram.requests.post", fake_post
    )
    adapter = TelegramAdapter(bot_token="tok", api_base="http://tg.local")
    external = adapter.send("привет", "555")
    assert external == "987"
    assert calls[0][0].endswith("/bottok/sendMessage")
    assert calls[0][1] == {"chat_id": "555", "text": "привет"}


def test_telegram_http_error(monkeypatch):
    class Resp:
        status_code = 429

        def json(self):
            return {"ok": False, "description": "Too Many Requests"}

    monkeypatch.setattr(
        "src.notifications.adapters.telegram.requests.post", lambda *a, **k: Resp()
    )
    with pytest.raises(AdapterError) as err:
        TelegramAdapter(bot_token="tok", api_base="http://tg.local").send("t", "1")
    assert "Too Many Requests" in str(err.value)


# --- MAX (заглушка по контракту) --------------------------------------------


def test_max_adapter_unavailable_and_raises():
    adapter = MaxAdapter()
    assert adapter.is_available() is False
    with pytest.raises(NotImplementedError):
        adapter.send("текст", "user")
    with pytest.raises(NotImplementedError):
        adapter.send_file("user", "file.pdf")


def test_max_channel_never_resolves(db, prescription, inspector):
    note = _make_pending(db, prescription, inspector)
    _add_channel(db, inspector.id, NotificationChannel.MAX, "max-1")
    assert resolve_channel(db, note) is None
    assert candidate_channels(db, note) == []


def test_max_bot_stub_raises_and_describes():
    stub = MaxBotStub()
    with pytest.raises(NotImplementedError):
        stub.handle_event({"type": "message"})
    contract = stub.describe_contract()
    assert contract["status"] == "not_connected"
    assert "/in_work" in contract["planned_commands"]


# --- STT (заглушка локального распознавания) --------------------------------


def test_stt_stub_raises_and_interface_ok():
    stub = StubTranscriber()
    assert isinstance(stub, Transcriber)
    with pytest.raises(NotImplementedError):
        transcribe("voice.wav")
