"""Команды ботов Telegram/MAX (Шаг 10). Слой не знает про транспорт.

Все действия идут только через сервисы (change_status, add_attachment,
add_comment) и статусную машину — прямой смены статусов в боте нет.
Клавиатура строится ИЗ правил TRANSITIONS, а не отдельным списком.

Команды Подрядчика: /start /my_prescriptions /open /in_work /fixed /comment
Команды Инспектора: /open /close /rework /reject
Фото: подписью (caption) считается номер предписания.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    Prescription,
    PrescriptionStatus,
    RecipientChannel,
    User,
    UserRole,
)
from src.db.repositories import get_prescription_by_number, list_prescriptions
from src.notifications.enums import NotificationChannel
from src.prescriptions import status_rules
from src.prescriptions.service import add_attachment, add_comment, change_status

S = PrescriptionStatus

ACTION_STATUS: dict[str, S] = {
    "in_work": S.IN_WORK,
    "fixed": S.FIXED_PENDING_REVIEW,
    "close": S.CLOSED,
    "rework": S.IN_WORK,
    "reject": S.REJECTED,
}

ACTION_LABELS: dict[str, str] = {
    "in_work": "Принять в работу",
    "fixed": "Сообщить об устранении",
    "close": "Закрыть",
    "rework": "Вернуть на доработку",
    "reject": "Отклонить",
}


@dataclass
class Update:
    chat_id: int
    from_user_id: int | None = None
    text: str | None = None
    kind: str = "message"
    callback_id: str | None = None
    callback_data: str | None = None
    photo_file_ids: list[str] = field(default_factory=list)


def link_user(db: Session, chat_id: int) -> User | None:
    row = db.scalar(
        select(RecipientChannel).where(
            RecipientChannel.recipient_type == "user",
            RecipientChannel.channel == NotificationChannel.TELEGRAM,
            RecipientChannel.address == str(chat_id),
            RecipientChannel.is_active.is_(True),
        )
    )
    if row is None:
        return None
    user = db.get(User, row.recipient_id)
    return user if user is not None and user.is_active else None


def allowed_actions(prescription: Prescription, user: User) -> list[str]:
    """Действия, разрешённые ролью пользователя в текущем статусе."""
    actions: list[str] = []
    for new_status, roles in status_rules.TRANSITIONS.get(prescription.status, {}).items():
        if user.role not in roles:
            continue
        if new_status == S.IN_WORK:
            action = (
                "rework"
                if prescription.status == S.FIXED_PENDING_REVIEW
                else "in_work"
            )
        elif new_status == S.FIXED_PENDING_REVIEW:
            action = "fixed"
        elif new_status == S.CLOSED:
            action = "close"
        elif new_status == S.REJECTED:
            action = "reject"
        else:
            continue
        if action not in actions:
            actions.append(action)
    return actions


def keyboard_for(prescription: Prescription, user: User) -> dict | None:
    actions = allowed_actions(prescription, user)
    if not actions:
        return None
    rows = [
        [{"text": ACTION_LABELS[a], "callback_data": f"act:{prescription.id}:{a}"}]
        for a in actions
    ]
    return {"inline_keyboard": rows}


def card_text(prescription: Prescription) -> str:
    p = prescription
    object_name = p.object.name if p.object else "—"
    contractor_name = p.contractor.name if p.contractor else "—"
    deadline = p.deadline.isoformat() if p.deadline else "не задан"
    lines = [
        f"Предписание № {p.doc_number} от {p.doc_date.isoformat()}",
        f"Тип: {p.prescription_type.value}",
        f"Объект: {object_name}",
        f"Подрядчик: {contractor_name}",
        f"Статус: {p.status.value}",
        f"Срок устранения: {deadline}",
        "",
        f"{p.description[:1000]}",
    ]
    for item in p.items[:20]:
        prefix = f"{item.item_number}. " if item.item_number else "— "
        lines.append(f"{prefix}{item.text[:300]}")
    if p.requires_review:
        lines.append("")
        lines.append("ВНИМАНИЕ: данные не проверены инспектором (черновик OCR/LLM).")
    return "\n".join(lines)


def help_text(user: User) -> str:
    common = "/start — этот список\n/open <номер> — карточка предписания"
    if user.role == UserRole.CONTRACTOR_RESPONSIBLE:
        return (
            "Команды подрядчика:\n"
            + common
            + "\n/my_prescriptions — мои предписания"
            "\n/in_work <номер> — взять в работу"
            "\n/fixed <номер> — сообщить об устранении"
            "\n/comment <номер> <текст> — комментарий"
            "\nФото: прикрепите с подписью = номер предписания."
        )
    if user.role in (UserRole.INSPECTOR, UserRole.ADMIN):
        return (
            "Команды инспектора:\n"
            + common
            + "\n/my_prescriptions — предписания в работе"
            "\n/close <номер> [комментарий] — закрыть"
            "\n/rework <номер> <комментарий> — вернуть на доработку"
            "\n/reject <номер> [причина] — отклонить"
        )
    return "Команды: " + common


def _find(db: Session, ref: str) -> Prescription | None:
    if not ref:
        return None
    ref = ref.strip()
    if ref.isdigit():
        found = db.get(Prescription, int(ref))
        if found is not None:
            return found
    return get_prescription_by_number(db, ref)


def process_update(
    db: Session,
    update: Update,
    *,
    send,
    answer_callback=None,
    fetch_photo=None,
) -> None:
    """Обработать одно событие. send(chat_id, text, keyboard=None)."""

    def reply(text: str, keyboard: dict | None = None) -> None:
        send(update.chat_id, text, keyboard)

    if update.kind == "callback":
        user = link_user(db, update.chat_id)
        if user is None:
            reply("Чат не привязан к пользователю системы.")
            return
        match = re.match(r"^act:(\d+):([a-z_]+)$", update.callback_data or "")
        if not match or match.group(2) not in ACTION_STATUS:
            _answer(answer_callback, update, "Неизвестная кнопка.")
            return
        prescription = db.get(Prescription, int(match.group(1)))
        if prescription is None:
            _answer(answer_callback, update, "Предписание не найдено.")
            return
        _do_change(
            db, prescription, match.group(2), user, None, reply,
            answer_callback, update,
        )
        return

    text = (update.text or "").strip()

    if update.photo_file_ids:
        user = link_user(db, update.chat_id)
        if user is None:
            reply("Чат не привязан к пользователю системы.")
            return
        _handle_photo(db, update, text, user, reply, fetch_photo)
        return

    match = re.match(r"^/([A-Za-z_]+)(?:@\w+)?(?:\s+(.*))?$", text, re.DOTALL)
    if not match:
        reply("Не распознано сообщение. /start — список команд.")
        return
    command = match.group(1).lower()
    rest = (match.group(2) or "").strip()

    user = link_user(db, update.chat_id)
    if user is None:
        reply(
            "Чат не привязан к пользователю. "
            "Попросите администратора внести ваш chat_id в контакты системы."
        )
        return

    handler = _COMMANDS.get(command)
    if handler is None:
        reply("Неизвестная команда.\n\n" + help_text(user))
        return
    handler(db, user, rest, reply)


def _answer(answer_callback, update: Update, text: str) -> None:
    if answer_callback is not None and update.callback_id is not None:
        answer_callback(update.callback_id, text)


def _do_change(
    db: Session,
    prescription: Prescription,
    action: str,
    user: User,
    comment: str | None,
    reply,
    answer_callback=None,
    update: Update | None = None,
) -> None:
    target = ACTION_STATUS[action]
    try:
        p = change_status(db, prescription, target, user, comment=comment)
    except (PermissionError, ValueError) as exc:
        text = f"Ошибка: {exc}"
    else:
        text = f"Готово: № {p.doc_number} → {p.status.value}."
    if update is not None and update.kind == "callback":
        _answer(answer_callback, update, text)
    reply(text, keyboard_for(prescription, user) if "Ошибка" not in text else None)


def _handle_photo(
    db: Session, update: Update, caption: str, user: User, reply, fetch_photo
) -> None:
    if fetch_photo is None:
        reply("Приём фото не настроен.")
        return
    match = re.match(r"^№?\s*(\S+)(?:\s+(.*))?$", caption or "", re.DOTALL)
    if not match:
        reply(
            "Прикрепите фото с подписью — номером предписания, "
            "например: П-14/2026"
        )
        return
    prescription = _find(db, match.group(1))
    if prescription is None:
        reply("Предписание не найдено по номеру в подписи фото.")
        return
    import tempfile
    import os

    data, filename = fetch_photo(update.photo_file_ids[0])
    suffix = os.path.splitext(filename or "")[1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(data)
        tmp.close()
        add_attachment(
            db,
            prescription,
            source_path=tmp.name,
            actor_user_id=user.id,
            caption=(match.group(2) or caption or None),
        )
    except (PermissionError, ValueError) as exc:
        reply(f"Ошибка: {exc}")
        return
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    reply(f"Фото добавлено к предписанию № {prescription.doc_number}.")


# --- команды ---


def _cmd_start(db, user, rest, reply):
    reply(help_text(user))


def _cmd_my(db, user, rest, reply):
    open_statuses = {
        S.NEW,
        S.UNDER_REVIEW,
        S.IN_WORK,
        S.FIXED_PENDING_REVIEW,
    }
    if user.role == UserRole.CONTRACTOR_RESPONSIBLE:
        items = list_prescriptions(db, contractor_id=user.contractor_id)
    elif user.role in (UserRole.INSPECTOR, UserRole.ADMIN):
        items = list_prescriptions(db)
        if user.role == UserRole.INSPECTOR:
            items = [
                p
                for p in items
                if p.inspector_user_id in (None, user.id)
            ]
    else:
        reply("Для этой роли списка нет.")
        return
    items = [p for p in items if p.status in open_statuses]
    if not items:
        reply("Открытых предписаний нет.")
        return
    lines = [
        f"№ {p.doc_number} · {p.status.value} · срок "
        f"{p.deadline.isoformat() if p.deadline else '—'}"
        for p in items
    ]
    reply("Предписания:\n" + "\n".join(lines))


def _cmd_open(db, user, rest, reply):
    prescription = _find(db, rest)
    if prescription is None:
        reply("Предписание не найдено. Укажите номер, например: /open П-14/2026")
        return
    reply(card_text(prescription), keyboard_for(prescription, user))


def _one_arg_prescription(db, rest, reply):
    prescription = _find(db, rest)
    if prescription is None:
        reply("Предписание не найдено. Формат: команда <номер>")
        return None
    return prescription


def _cmd_in_work(db, user, rest, reply):
    prescription = _one_arg_prescription(db, rest, reply)
    if prescription is None:
        return
    _do_change(db, prescription, "in_work", user, None, reply)


def _cmd_fixed(db, user, rest, reply):
    prescription = _one_arg_prescription(db, rest, reply)
    if prescription is None:
        return
    _do_change(db, prescription, "fixed", user, None, reply)


def _split_number_and_comment(rest: str) -> tuple[str, str | None]:
    if not rest:
        return "", None
    number, _, comment = rest.partition(" ")
    return number.strip(), (comment.strip() or None)


def _cmd_comment(db, user, rest, reply):
    number, comment = _split_number_and_comment(rest)
    prescription = _find(db, number)
    if prescription is None or comment is None:
        reply("Формат: /comment <номер> <текст>")
        return
    try:
        add_comment(db, prescription, actor_user_id=user.id, text=comment)
    except (PermissionError, ValueError) as exc:
        reply(f"Ошибка: {exc}")
        return
    reply("Комментарий добавлен.")


def _cmd_close(db, user, rest, reply):
    number, comment = _split_number_and_comment(rest)
    prescription = _one_arg_prescription(db, number, reply)
    if prescription is None:
        return
    _do_change(db, prescription, "close", user, comment, reply)


def _cmd_rework(db, user, rest, reply):
    number, comment = _split_number_and_comment(rest)
    if not comment:
        reply("При возврате на доработку обязателен комментарий: "
              "/rework <номер> <комментарий>")
        return
    prescription = _one_arg_prescription(db, number, reply)
    if prescription is None:
        return
    _do_change(db, prescription, "rework", user, comment, reply)


def _cmd_reject(db, user, rest, reply):
    number, comment = _split_number_and_comment(rest)
    prescription = _one_arg_prescription(db, number, reply)
    if prescription is None:
        return
    _do_change(db, prescription, "reject", user, comment, reply)


_COMMANDS = {
    "start": _cmd_start,
    "help": _cmd_start,
    "my_prescriptions": _cmd_my,
    "open": _cmd_open,
    "in_work": _cmd_in_work,
    "fixed": _cmd_fixed,
    "comment": _cmd_comment,
    "close": _cmd_close,
    "rework": _cmd_rework,
    "reject": _cmd_reject,
}
