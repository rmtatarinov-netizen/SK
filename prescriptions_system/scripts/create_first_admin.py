"""Создание первого администратора системы (Шаг 3).

Интерактивный режим:
    python scripts/create_first_admin.py

Env-режим (для автоматизации): задать FIRST_ADMIN_LOGIN, FIRST_ADMIN_PASSWORD,
FIRST_ADMIN_FULL_NAME, FIRST_ADMIN_EMAIL и запустить скрипт без stdin-TTY.

Если администратор в системе уже есть — скрипт завершается без изменений.
Пароль в базу пишется только bcrypt-хешем (passlib).
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from src.db.engine import get_session_factory  # noqa: E402
from src.db.models import User, UserRole  # noqa: E402
from src.users.service import hash_password  # noqa: E402


def _collect_params() -> dict[str, str]:
    login = os.environ.get("FIRST_ADMIN_LOGIN")
    if login:
        return {
            "login": login,
            "full_name": os.environ.get("FIRST_ADMIN_FULL_NAME", login),
            "email": os.environ.get("FIRST_ADMIN_EMAIL") or None,
            "password": os.environ.get("FIRST_ADMIN_PASSWORD"),
        }

    if not sys.stdin.isatty():
        raise SystemExit(
            "Не интерактивный режим и переменные FIRST_ADMIN_* не заданы."
        )
    print("Создание первого администратора системы.")
    login = input("Логин [admin]: ").strip() or "admin"
    full_name = input("ФИО: ").strip()
    email = input("Email (необязательно): ").strip() or None
    password = getpass.getpass("Пароль: ")
    password2 = getpass.getpass("Пароль ещё раз: ")
    if password != password2:
        raise SystemExit("Пароли не совпадают.")
    return {"login": login, "full_name": full_name, "email": email, "password": password}


def main() -> int:
    db = get_session_factory()()
    try:
        existing_admin = db.scalar(
            select(User).where(User.role == UserRole.ADMIN).limit(1)
        )
        if existing_admin is not None:
            print(
                f"Администратор уже существует (id={existing_admin.id}, "
                f"login={existing_admin.login}) — изменений не вносится."
            )
            return 0

        params = _collect_params()
        if not params["login"] or not params["password"]:
            print("Логин и пароль обязательны.", file=sys.stderr)
            return 2
        if len(params["password"]) < 8:
            print("Пароль должен быть не короче 8 символов.", file=sys.stderr)
            return 2
        if not params["full_name"]:
            print("ФИО обязательно.", file=sys.stderr)
            return 2

        user = User(
            login=params["login"],
            full_name=params["full_name"],
            role=UserRole.ADMIN,
            email=params["email"],
            password_hash=hash_password(params["password"]),
            must_change_password=False,
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"Создан администратор: id={user.id}, login={user.login}.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
