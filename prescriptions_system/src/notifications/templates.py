"""Тексты уведомлений (Шаг 8). Шаблоны соответствуют матрице событий ТЗ.

Ссылка на карточку — относительный путь веб-интерфейса (Шаг 9).
"""

from __future__ import annotations

from src.db.models import Prescription
from src.notifications.enums import NotificationEvent

_EVENT_HEADERS: dict[NotificationEvent, str] = {
    NotificationEvent.PRESCRIPTION_CREATED: (
        "Зарегистрировано новое предписание № {doc}."
    ),
    NotificationEvent.STATUS_CHANGED_TO_UNDER_REVIEW: (
        "Предписание № {doc} передано на проверку инспектору."
    ),
    NotificationEvent.STATUS_CHANGED_TO_IN_WORK: (
        "Предписание № {doc} переведено в статус «В работе»."
    ),
    NotificationEvent.STATUS_CHANGED_TO_FIXED_PENDING_REVIEW: (
        "Подрядчик сообщил об устранении по предписанию № {doc}."
    ),
    NotificationEvent.RETURNED_TO_IN_WORK: (
        "Предписание № {doc} возвращено на доработку."
    ),
    NotificationEvent.PRESCRIPTION_CLOSED: (
        "Предписание № {doc} закрыто: устранение подтверждено."
    ),
    NotificationEvent.PRESCRIPTION_REJECTED: (
        "Предписание № {doc} отклонено."
    ),
    NotificationEvent.DEADLINE_CHANGED: "Изменён срок устранения по предписанию № {doc}.",
    NotificationEvent.RESPONSIBLE_CHANGED: "Сменён ответственный по предписанию № {doc}.",
    NotificationEvent.ATTACHMENT_ADDED: "К предписанию № {doc} добавлено вложение.",
    NotificationEvent.INTERNAL_PRINT_VERSION_GENERATED: (
        "Сформирована печатная версия внутреннего предписания № {doc}."
    ),
    NotificationEvent.INTERNAL_SIGNED_SCAN_UPLOADED: (
        "Загружен подписанный скан предписания № {doc}."
    ),
    NotificationEvent.INTERNAL_SIGNED_SCAN_VERIFIED: (
        "Подписанный скан предписания № {doc} проверен."
    ),
    NotificationEvent.DEADLINE_APPROACHING: "Приближается срок устранения по предписанию № {doc}.",
    NotificationEvent.DEADLINE_OVERDUE: "Просрочен срок устранения по предписанию № {doc}!",
}


def render_message(
    event: NotificationEvent, prescription: Prescription, comment: str | None = None
) -> str:
    header = _EVENT_HEADERS.get(
        event, "Событие {event} по предписанию № {doc}."
    ).format(doc=prescription.doc_number, event=event.value)

    lines = [header, ""]
    lines.append(f"Объект: {prescription.object.name if prescription.object else '—'}")
    lines.append(
        f"Подрядчик: {prescription.contractor.name if prescription.contractor else '—'}"
    )
    if event in (
        NotificationEvent.DEADLINE_CHANGED,
        NotificationEvent.DEADLINE_APPROACHING,
        NotificationEvent.DEADLINE_OVERDUE,
        NotificationEvent.PRESCRIPTION_CREATED,
        NotificationEvent.RETURNED_TO_IN_WORK,
    ):
        deadline = (
            prescription.deadline.isoformat() if prescription.deadline else "не задан"
        )
        lines.append(f"Срок устранения: {deadline}")
    if event == NotificationEvent.STATUS_CHANGED_TO_IN_WORK:
        lines.append(f"Дата начала работ: {prescription.updated_at.date().isoformat()}")
    if event == NotificationEvent.RESPONSIBLE_CHANGED:
        user = prescription.responsible_user
        lines.append(f"Новый ответственный: {user.full_name if user else '—'}")
    lines.append(f"Статус: {prescription.status.value}")
    if comment:
        lines.append("")
        lines.append(f"Комментарий: {comment}")
    lines.append("")
    lines.append(f"Карточка: /prescriptions/{prescription.id}")
    return "\n".join(lines)
