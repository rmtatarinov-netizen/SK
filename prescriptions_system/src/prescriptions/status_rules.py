"""Статусная машина предписаний (Шаг 4). Единственный источник допустимых переходов.

Переход closed/rejected в любое другое состояние — только администратор.
draft_for_signature → new — только инспектор и только после загрузки и
проверки подписанного скана. Возврат на доработку
(fixed_pending_review → in_work) — с обязательным комментарием.
"""

from __future__ import annotations

from src.db.models import (
    Prescription,
    PrescriptionStatus,
    User,
    UserRole,
)
from src.notifications.enums import NotificationEvent

S = PrescriptionStatus

TRANSITIONS: dict[PrescriptionStatus, dict[PrescriptionStatus, tuple[UserRole, ...]]] = {
    S.DRAFT_FOR_SIGNATURE: {
        S.NEW: (UserRole.INSPECTOR,),
    },
    S.NEW: {
        S.UNDER_REVIEW: (UserRole.INSPECTOR,),
        S.IN_WORK: (UserRole.CONTRACTOR_RESPONSIBLE, UserRole.INSPECTOR),
        S.REJECTED: (UserRole.INSPECTOR,),
    },
    S.UNDER_REVIEW: {
        S.IN_WORK: (UserRole.INSPECTOR,),
        S.REJECTED: (UserRole.INSPECTOR,),
    },
    S.IN_WORK: {
        S.FIXED_PENDING_REVIEW: (UserRole.CONTRACTOR_RESPONSIBLE,),
        S.REJECTED: (UserRole.INSPECTOR,),
    },
    S.FIXED_PENDING_REVIEW: {
        S.CLOSED: (UserRole.INSPECTOR,),
        # возврат на доработку: обязателен комментарий инспектора
        S.IN_WORK: (UserRole.INSPECTOR,),
    },
    S.CLOSED: {},
    S.REJECTED: {},
}

# Подрядчик выполняет переходы только по своим предписаниям.
CONTRACTOR_OWNED_TRANSITIONS = {
    (S.NEW, S.IN_WORK),
    (S.IN_WORK, S.FIXED_PENDING_REVIEW),
}

# Только возврат инспектором (fixed_pending_review → in_work) требует
# комментарий; обратный переход подрядчика — нет.
REQUIRED_COMMENT_TRANSITIONS = {
    (S.FIXED_PENDING_REVIEW, S.IN_WORK),
}

STATUS_EVENTS: dict[tuple[PrescriptionStatus, PrescriptionStatus], NotificationEvent] = {
    (S.DRAFT_FOR_SIGNATURE, S.NEW): NotificationEvent.PRESCRIPTION_CREATED,
    (S.NEW, S.UNDER_REVIEW): NotificationEvent.STATUS_CHANGED_TO_UNDER_REVIEW,
    (S.NEW, S.IN_WORK): NotificationEvent.STATUS_CHANGED_TO_IN_WORK,
    (S.UNDER_REVIEW, S.IN_WORK): NotificationEvent.STATUS_CHANGED_TO_IN_WORK,
    (S.IN_WORK, S.FIXED_PENDING_REVIEW): NotificationEvent.STATUS_CHANGED_TO_FIXED_PENDING_REVIEW,
    (S.FIXED_PENDING_REVIEW, S.IN_WORK): NotificationEvent.RETURNED_TO_IN_WORK,
    (S.FIXED_PENDING_REVIEW, S.CLOSED): NotificationEvent.PRESCRIPTION_CLOSED,
    (S.NEW, S.REJECTED): NotificationEvent.PRESCRIPTION_REJECTED,
    (S.UNDER_REVIEW, S.REJECTED): NotificationEvent.PRESCRIPTION_REJECTED,
    (S.IN_WORK, S.REJECTED): NotificationEvent.PRESCRIPTION_REJECTED,
}


def build_status_event(
    *, old_status: PrescriptionStatus, new_status: PrescriptionStatus
) -> NotificationEvent | None:
    return STATUS_EVENTS.get((old_status, new_status))


def validate_status_transition(
    *,
    old_status: PrescriptionStatus,
    new_status: PrescriptionStatus,
    changed_by_user: User,
    prescription: Prescription,
    comment: str | None = None,
) -> None:
    """Raise PermissionError/ValueError, если переход запрещён."""
    if old_status == new_status:
        raise ValueError("Новый статус совпадает с текущим")

    if not changed_by_user.is_active:
        raise PermissionError("Неактивный пользователь не может менять статус")

    if old_status in (S.CLOSED, S.REJECTED):
        if changed_by_user.role != UserRole.ADMIN:
            raise PermissionError(
                "Изменять статус закрытого или отклонённого предписания "
                "может только администратор"
            )
        return

    allowed_roles = TRANSITIONS.get(old_status, {}).get(new_status)
    if allowed_roles is None:
        raise PermissionError(
            f"Переход {old_status.value} → {new_status.value} не допускается"
        )
    if changed_by_user.role not in allowed_roles:
        raise PermissionError(
            f"Роль {changed_by_user.role.value} не может выполнить переход "
            f"{old_status.value} → {new_status.value}"
        )

    if (
        changed_by_user.role == UserRole.CONTRACTOR_RESPONSIBLE
        and (old_status, new_status) in CONTRACTOR_OWNED_TRANSITIONS
        and (
            changed_by_user.contractor_id is None
            or changed_by_user.contractor_id != prescription.contractor_id
        )
    ):
        raise PermissionError("Подрядчик может изменять только свои предписания")

    if (old_status, new_status) in REQUIRED_COMMENT_TRANSITIONS:
        if not comment or not comment.strip():
            raise ValueError(
                "Возврат на доработку требует комментария инспектора"
            )

    if old_status == S.DRAFT_FOR_SIGNATURE and new_status == S.NEW:
        if prescription.signed_scan_verified_at is None:
            raise ValueError(
                "Регистрация внутреннего предписания возможна только после "
                "загрузки и проверки подписанного скана"
            )
