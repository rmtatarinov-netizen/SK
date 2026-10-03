"""Генерация текстов уведомлений (начало, Шаг 8)."""

from __future__ import annotations

from src.db.models import Prescription
from src.notifications.enums import NotificationEvent

_EVENT_TITLES: dict[NotificationEvent, str] = {
    NotificationEvent.PRESCRIPTION_CREATED: "Зарегистрировано новое предписание",
    NotificationEvent.STATUS_CHANGED_TO_UNDER_REVIEW: "Предписание передано на проверку",
    NotificationEvent.STATUS_CHANGED_TO_IN_WORK: "Предписание переведено в работу",
    NotificationEvent.STATUS_CHANGED_TO_FIXED_PENDING_REVIEW: "Заявлено об устранении",
    NotificationEvent.RETURNED_TO_IN_WORK: "Предписание возвращено на доработку",
    NotificationEvent.PRESCRIPTION_CLOSED: "Предписание закрыто",
    NotificationEvent.PRESCRIPTION_REJECTED: "Предписание отклонено",
    NotificationEvent.DEADLINE_CHANGED: "Изменён срок устранения",
    NotificationEvent.RESPONSIBLE_CHANGED: "Сменён ответственный",
    NotificationEvent.ATTACHMENT_ADDED: "Добавлено вложение",
    NotificationEvent.INTERNAL_PRINT_VERSION_GENERATED: "Сформирована печатная версия",
    NotificationEvent.INTERNAL_SIGNED_SCAN_UPLOADED: "Загружен подписанный скан",
    NotificationEvent.INTERNAL_SIGNED_SCAN_VERIFIED: "Подписанный скан проверен",
    NotificationEvent.DEADLINE_APPROACHING: "Приближается срок устранения",
    NotificationEvent.DEADLINE_OVERDUE: "Срок устранения просрочен",
}


def render_message(
    event: NotificationEvent, prescription: Prescription, comment: str | None = None
) -> str:
    """Базовый текст. Персонализация по событию — на Шаге 8."""
    lines = [
        f"{_EVENT_TITLES.get(event, event.value)}: № {prescription.doc_number} "
        f"от {prescription.doc_date.isoformat()}.",
        f"Объект: {prescription.object.name if prescription.object else '—'}",
        f"Статус: {prescription.status.value}",
    ]
    if comment:
        lines.append(f"Комментарий: {comment}")
    lines.append(f"Карточка: /prescriptions/{prescription.id}")
    return "\n".join(lines)
