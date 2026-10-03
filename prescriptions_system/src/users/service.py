"""Управление пользователями и ролями (Шаг 3).

Правила:
- только администратор создаёт учётные записи, меняет роли и блокирует;
- самостоятельная регистрация и самостоятельная смена роли запрещены;
- хотя бы один активный администратор обязан оставаться в системе;
- все смены ролей логируются в user_role_change_log;
- контакты Telegram/MAX хранятся в recipient_channels, а не полями в users;
- временный пароль возвращается администратору один раз, при первом входе
  пользователем обязательна смена пароля (must_change_password).
"""

from __future__ import annotations

import csv
import secrets
from pathlib import Path

from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Contractor,
    Object,
    RecipientChannel,
    User,
    UserRole,
    UserRoleChangeLog,
)
from src.notifications.enums import NotificationChannel
from src.users.schemas import UserCreate

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def generate_temp_password() -> str:
    return secrets.token_urlsafe(12)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def _require_admin(db: Session, actor_user_id: int | None) -> User:
    if actor_user_id is None:
        raise PermissionError("Только администратор может управлять пользователями")
    actor = db.get(User, actor_user_id)
    if actor is None or not actor.is_active or actor.role != UserRole.ADMIN:
        raise PermissionError("Только администратор может управлять пользователями")
    return actor


def _count_active_admins(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        )
        or 0
    )


def set_recipient_channel(
    db: Session,
    *,
    recipient_id: int,
    channel: NotificationChannel,
    address: str,
    is_preferred: bool = False,
) -> RecipientChannel:
    """Создать/обновить активный контакт. Идемпотентно по уникальному ключу."""
    existing = db.scalar(
        select(RecipientChannel).where(
            RecipientChannel.recipient_type == "user",
            RecipientChannel.recipient_id == recipient_id,
            RecipientChannel.channel == channel,
            RecipientChannel.address == address,
        )
    )
    if existing is not None:
        existing.is_active = True
        existing.is_preferred = is_preferred
        return existing
    record = RecipientChannel(
        recipient_type="user",
        recipient_id=recipient_id,
        channel=channel,
        address=address,
        is_preferred=is_preferred,
    )
    db.add(record)
    return record


def create_user(
    db: Session,
    data: UserCreate,
    *,
    actor_user_id: int | None,
) -> tuple[User, str]:
    """Создать пользователя (только администратор).

    Возвращает (user, temp_password): временный пароль известен только
    администратору, пользователю выдаётся извне, при первом входе обязательна
    смена пароля.
    """
    _require_admin(db, actor_user_id)

    if db.scalar(select(User.id).where(User.login == data.login)):
        raise ValueError(f"Пользователь с логином {data.login} уже существует")

    if data.contractor_id is not None and db.get(Contractor, data.contractor_id) is None:
        raise ValueError("Подрядчик не найден")
    if data.object_id is not None and db.get(Object, data.object_id) is None:
        raise ValueError("Объект не найден")

    temp_password = generate_temp_password()
    user = User(
        login=data.login,
        full_name=data.full_name,
        role=data.role,
        email=data.email,
        phone=data.phone,
        preferred_notification_channel=data.preferred_notification_channel,
        contractor_id=data.contractor_id,
        object_id=data.object_id,
        password_hash=hash_password(temp_password),
        must_change_password=True,
        is_active=True,
    )
    db.add(user)
    db.flush()

    if data.telegram_chat_id:
        set_recipient_channel(
            db,
            recipient_id=user.id,
            channel=NotificationChannel.TELEGRAM,
            address=data.telegram_chat_id,
            is_preferred=data.preferred_notification_channel == NotificationChannel.TELEGRAM,
        )
    if data.max_user_id:
        set_recipient_channel(
            db,
            recipient_id=user.id,
            channel=NotificationChannel.MAX,
            address=data.max_user_id,
            is_preferred=data.preferred_notification_channel == NotificationChannel.MAX,
        )

    db.commit()
    db.refresh(user)
    return user, temp_password


def authenticate(db: Session, login: str, password: str) -> User | None:
    """Проверка учётных данных. Неактивный пользователь не входит."""
    user = db.scalar(select(User).where(User.login == login, User.is_active.is_(True)))
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def change_password(
    db: Session, *, user_id: int, new_password: str, actor_user_id: int | None
) -> User:
    """Смена пароля: самим пользователем или администратором."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("Пользователь не найден")
    if len(new_password) < 8:
        raise ValueError("Пароль слишком короткий (минимум 8 символов)")
    if actor_user_id != user_id:
        _require_admin(db, actor_user_id)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.commit()
    db.refresh(user)
    return user


def change_user_role(
    db: Session,
    user_id: int,
    new_role: UserRole,
    changed_by_user_id: int,
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("Пользователь не найден")

    changed_by = _require_admin(db, changed_by_user_id)
    if user.id == changed_by.id:
        raise PermissionError("Самостоятельная смена роли запрещена")

    old_role = user.role
    if old_role == new_role:
        return user

    if old_role == UserRole.ADMIN and _count_active_admins(db) <= 1:
        raise ValueError("Нельзя снять роль с последнего активного администратора")

    user.role = new_role
    db.add(
        UserRoleChangeLog(
            user_id=user.id,
            old_role=old_role.value,
            new_role=new_role.value,
            changed_by_user_id=changed_by_user_id,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def deactivate_user(db: Session, user_id: int, changed_by_user_id: int) -> User:
    """Блокировка входа; история предписаний сохраняется. Только администратор."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("Пользователь не найден")

    changed_by = _require_admin(db, changed_by_user_id)
    if user.id == changed_by.id:
        raise PermissionError("Нельзя заблокировать самого себя")

    if not user.is_active:
        return user

    if user.role == UserRole.ADMIN and _count_active_admins(db) <= 1:
        raise ValueError("Нельзя заблокировать последнего активного администратора")

    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


def import_users_from_csv(
    db: Session, csv_path: Path | str, *, actor_user_id: int | None
) -> dict:
    """CSV-импорт пользователей (только администратор).

    Колонки: login, full_name, role, email, phone, contractor, object,
    telegram_chat_id, max_user_id. Подрядчик/объект находятся по имени;
    отсутствующие справочники создаются. Существующий логин — пропуск
    (идемпотентность повторного импорта).
    """
    _require_admin(db, actor_user_id)

    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    new_objects: list[str] = []
    new_contractors: list[str] = []

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        required = {"login", "full_name", "role"}
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise ValueError(
                "CSV должен содержать колонки: login, full_name, role"
            )

        for line_no, row in enumerate(reader, start=2):
            login = (row.get("login") or "").strip()
            full_name = (row.get("full_name") or "").strip()
            role_raw = (row.get("role") or "").strip()
            try:
                if not login or not full_name or not role_raw:
                    raise ValueError("Пустые обязательные поля")
                role = UserRole(role_raw)
                _resolve_or_create_contractor(db, row, new_contractors)
                _resolve_or_create_object(db, row, new_objects)
                data = UserCreate(
                    login=login,
                    full_name=full_name,
                    role=role,
                    email=(row.get("email") or "").strip() or None,
                    phone=(row.get("phone") or "").strip() or None,
                    contractor_id=_lookup_contractor_id(db, row),
                    object_id=_lookup_object_id(db, row),
                    telegram_chat_id=(row.get("telegram_chat_id") or "").strip() or None,
                    max_user_id=(row.get("max_user_id") or "").strip() or None,
                )
                if db.scalar(select(User.id).where(User.login == login)):
                    skipped.append(login)
                    continue
                create_user(db, data, actor_user_id=actor_user_id)
                created.append(login)
            except Exception as exc:
                db.rollback()
                errors.append(f"строка {line_no} ({login or 'без логина'}): {exc}")

    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "new_objects": new_objects,
        "new_contractors": new_contractors,
    }


def _resolve_or_create_contractor(
    db: Session, row: dict, created_list: list[str]
) -> None:
    name = (row.get("contractor") or "").strip()
    if not name:
        return
    existing = db.scalar(select(Contractor.id).where(Contractor.name == name))
    if existing is None:
        db.add(Contractor(name=name))
        db.flush()
        created_list.append(name)


def _resolve_or_create_object(db: Session, row: dict, created_list: list[str]) -> None:
    name = (row.get("object") or "").strip()
    if not name:
        return
    existing = db.scalar(select(Object.id).where(Object.name == name))
    if existing is None:
        db.add(Object(name=name))
        db.flush()
        created_list.append(name)


def _lookup_contractor_id(db: Session, row: dict) -> int | None:
    name = (row.get("contractor") or "").strip()
    if not name:
        return None
    return db.scalar(select(Contractor.id).where(Contractor.name == name))


def _lookup_object_id(db: Session, row: dict) -> int | None:
    name = (row.get("object") or "").strip()
    if not name:
        return None
    return db.scalar(select(Object.id).where(Object.name == name))
