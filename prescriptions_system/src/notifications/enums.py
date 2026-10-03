"""Перечисления подсистемы уведомлений (Шаг 8). Модели БД ссылаются на них."""

from enum import Enum as PyEnum


class NotificationChannel(str, PyEnum):
    TELEGRAM = "telegram"
    MAX = "max"


class NotificationStatus(str, PyEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    NO_CHANNEL = "no_channel"
    CANCELLED = "cancelled"


class NotificationEvent(str, PyEnum):
    PRESCRIPTION_CREATED = "prescription_created"
    STATUS_CHANGED_TO_UNDER_REVIEW = "status_changed_to_under_review"
    STATUS_CHANGED_TO_IN_WORK = "status_changed_to_in_work"
    STATUS_CHANGED_TO_FIXED_PENDING_REVIEW = "status_changed_to_fixed_pending_review"
    RETURNED_TO_IN_WORK = "returned_to_in_work"
    PRESCRIPTION_CLOSED = "prescription_closed"
    PRESCRIPTION_REJECTED = "prescription_rejected"
    DEADLINE_CHANGED = "deadline_changed"
    RESPONSIBLE_CHANGED = "responsible_changed"
    ATTACHMENT_ADDED = "attachment_added"
    INTERNAL_PRINT_VERSION_GENERATED = "internal_print_version_generated"
    INTERNAL_SIGNED_SCAN_UPLOADED = "internal_signed_scan_uploaded"
    INTERNAL_SIGNED_SCAN_VERIFIED = "internal_signed_scan_verified"
    DEADLINE_APPROACHING = "deadline_approaching"
    DEADLINE_OVERDUE = "deadline_overdue"
