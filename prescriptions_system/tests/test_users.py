"""Тесты пользователей и ролей (Шаг 3)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.db.models import (
    Contractor,
    Object,
    RecipientChannel,
    User,
    UserRole,
    UserRoleChangeLog,
)
from src.notifications.enums import NotificationChannel
from src.users import service
from src.users.schemas import UserCreate


def _create(db, login: str, role: UserRole, actor: User, **kw) -> tuple[User, str]:
    data = UserCreate(login=login, full_name=f"Тест {login}", role=role, **kw)
    return service.create_user(db, data, actor_user_id=actor.id)


def test_only_admin_can_create(db, inspector):
    data = UserCreate(login="user1", full_name="Юзер Один", role=UserRole.MANAGER)
    with pytest.raises(PermissionError):
        service.create_user(db, data, actor_user_id=inspector.id)


def test_login_unique(db, admin):
    _create(db, "dup1", UserRole.INSPECTOR, admin)
    with pytest.raises(ValueError):
        _create(db, "dup1", UserRole.INSPECTOR, admin)


def test_contacts_stored_in_recipient_channels(db, admin):
    user, _ = _create(
        db,
        "tg_user",
        UserRole.CONTRACTOR_RESPONSIBLE,
        admin,
        telegram_chat_id="123456",
        preferred_notification_channel=NotificationChannel.TELEGRAM,
    )
    channels = db.scalars(
        select(RecipientChannel).where(
            RecipientChannel.recipient_type == "user",
            RecipientChannel.recipient_id == user.id,
        )
    ).all()
    assert len(channels) == 1
    assert channels[0].channel == NotificationChannel.TELEGRAM
    assert channels[0].address == "123456"
    assert channels[0].is_preferred is True


def test_authenticate_and_first_login_password_change(db, admin):
    user, temp_password = _create(db, "authu", UserRole.MANAGER, admin)
    assert authenticate_ok(db, user.login, temp_password)

    with pytest.raises(ValueError):
        service.change_password(db, user_id=user.id, new_password="short", actor_user_id=user.id)

    service.change_password(
        db, user_id=user.id, new_password="N3w$ecurePass", actor_user_id=user.id
    )
    db.refresh(user)
    assert user.must_change_password is False
    assert authenticate_ok(db, user.login, "N3w$ecurePass")
    assert service.authenticate(db, user.login, temp_password) is None


def authenticate_ok(db, login: str, password: str) -> bool:
    user = service.authenticate(db, login, password)
    return user is not None and user.is_active


def test_role_change_requires_admin_and_logs(db, admin, inspector):
    with pytest.raises(PermissionError):
        service.change_user_role(db, admin.id, UserRole.MANAGER, inspector.id)

    updated = service.change_user_role(db, inspector.id, UserRole.MANAGER, admin.id)
    assert updated.role == UserRole.MANAGER
    log = db.scalars(select(UserRoleChangeLog)).one()
    assert log.user_id == inspector.id
    assert log.old_role == "inspector"
    assert log.new_role == "manager"
    assert log.changed_by_user_id == admin.id


def test_self_role_change_forbidden(db, admin):
    with pytest.raises(PermissionError):
        service.change_user_role(db, admin.id, UserRole.ADMIN, admin.id)
    with pytest.raises(PermissionError):
        service.change_user_role(db, admin.id, UserRole.MANAGER, admin.id)


def test_last_admin_protection(db, admin):
    admin2, _ = _create(db, "admin2", UserRole.ADMIN, admin)

    # самостоятельная смена роли администратора запрещена
    with pytest.raises(PermissionError):
        service.change_user_role(db, admin.id, UserRole.MANAGER, admin.id)

    # блокировка одного из двух администраторов допустима
    service.deactivate_user(db, admin2.id, admin.id)

    # остался один активный администратор — снимать роль нельзя
    # (в том числе с уже неактивного администратора)
    with pytest.raises(ValueError):
        service.change_user_role(db, admin2.id, UserRole.MANAGER, admin.id)

    # нельзя заблокировать самого себя
    with pytest.raises(PermissionError):
        service.deactivate_user(db, admin.id, admin.id)


def test_deactivate_non_admin_ok_and_no_login(db, admin, contractor_user):
    service.deactivate_user(db, contractor_user.id, admin.id)
    db.refresh(contractor_user)
    assert contractor_user.is_active is False
    _, temp = ("x", "y")  # аутентификация несуществующего пароля не нужна
    assert service.authenticate(db, contractor_user.login, "whatever") is None


def test_csv_import_and_idempotency(db, admin, tmp_path):
    csv_file = tmp_path / "users.csv"
    csv_file.write_text(
        "login,full_name,role,email,phone,contractor,object,telegram_chat_id,max_user_id\n"
        "csv_user1,Импортный Один,contractor_responsible,one@company.ru,,ООО Импорт-Строй,Объект-X,777,\n"
        "csv_user2,Импортный Два,manager,,,ООО Импорт-Строй,,,,\n"
        "csv_user1,Дубль,manager,,,,,,\n"
        "csv_bad,Без Роли,,,,,,,\n",
        encoding="utf-8",
    )
    result = service.import_users_from_csv(db, csv_file, actor_user_id=admin.id)
    assert result["created"] == ["csv_user1", "csv_user2"]
    assert result["skipped"] == ["csv_user1"]
    assert len(result["errors"]) == 1
    assert "csv_user2" not in result["errors"][0]
    assert "ООО Импорт-Строй" in result["new_contractors"]
    assert "Объект-X" in result["new_objects"]

    contractor = db.scalar(select(Contractor).where(Contractor.name == "ООО Импорт-Строй"))
    assert contractor is not None
    obj = db.scalar(select(Object).where(Object.name == "Объект-X"))
    assert obj is not None
    imported = db.scalar(select(User).where(User.login == "csv_user1"))
    assert imported.contractor_id == contractor.id
    assert imported.object_id == obj.id

    # повторный импорт того же файла — идемпотентность
    result2 = service.import_users_from_csv(db, csv_file, actor_user_id=admin.id)
    assert result2["created"] == []
    assert set(result2["skipped"]) == {"csv_user1", "csv_user2"}
    assert result2["new_contractors"] == []


def test_csv_import_requires_admin(db, inspector, tmp_path):
    csv_file = tmp_path / "users.csv"
    csv_file.write_text("login,full_name,role\nx1,Ик Один,manager\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        service.import_users_from_csv(db, csv_file, actor_user_id=inspector.id)
