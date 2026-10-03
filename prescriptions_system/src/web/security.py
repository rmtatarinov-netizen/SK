"""Сессийная авторизация и RBAC (Шаг 9).

Вход — только по учётным записям из таблицы users (bcrypt), самостоятельная
регистрация отсутствует. 401 → редирект на /login (обработчик в app.py),
403 — доступ запрещён. Пользователь с must_change_password не может работать,
пока не сменит пароль (451 → /password).
"""

from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.db.models import User, UserRole
from src.web.deps import get_db

USER_ID_KEY = "user_id"

OPEN_PATHS = {"/login", "/logout", "/password", "/static"}


class MustChangePasswordError(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get(USER_ID_KEY)
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        return None
    return user


def require_login(
    request: Request, user: User | None = Depends(current_user)
) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    path = request.url.path
    if user.must_change_password and not any(
        path == p or path.startswith(p + "/") for p in ("/password", "/logout", "/static")
    ):
        raise MustChangePasswordError()
    return user


def require_role(*roles: UserRole) -> Callable:
    allowed = set(roles)

    def dependency(user: User = Depends(require_login)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user

    return dependency
