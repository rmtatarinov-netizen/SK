"""Схемы пользователей (Pydantic). Валидация входных данных сервисного слоя."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from src.db.models import UserRole
from src.notifications.enums import NotificationChannel

LOGIN_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$"


class UserCreate(BaseModel):
    login: str = Field(pattern=LOGIN_PATTERN)
    full_name: str = Field(min_length=2, max_length=255)
    role: UserRole
    email: str | None = None
    phone: str | None = None
    contractor_id: int | None = None
    object_id: int | None = None
    preferred_notification_channel: NotificationChannel | None = None
    telegram_chat_id: str | None = None
    max_user_id: str | None = None

    @field_validator("email")
    @classmethod
    def _email_simple(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value and ("@" not in value or len(value) > 255):
            raise ValueError("Некорректный email")
        return value or None

    @field_validator("phone", "telegram_chat_id", "max_user_id")
    @classmethod
    def _strip_or_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ContactBind(BaseModel):
    """Привязка контакта получателя (хранится в recipient_channels)."""

    channel: NotificationChannel
    address: str = Field(min_length=1, max_length=128)
    is_preferred: bool = False
