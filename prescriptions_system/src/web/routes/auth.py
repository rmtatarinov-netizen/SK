"""Страницы входа и смены пароля (Шаг 9).

Самостоятельная регистрация не предусмотрена: вход только по учёткам из
таблицы users (bcrypt). После входа при must_change_password пользователь
принудительно ведётся на /password.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from src.db.models import User
from src.users import service as users_service
from src.web.deps import flash, get_db, render
from src.web.security import USER_ID_KEY, current_user, require_login

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    if current_user(request, db) is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    login: str = Form(...),
    password: str = Form(...),
):
    user = users_service.authenticate(db, login.strip(), password)
    if user is None:
        flash(request, "Неверный логин или пароль (или пользователь заблокирован).")
        return render(request, "login.html")
    request.session[USER_ID_KEY] = user.id
    if user.must_change_password:
        flash(request, "Требуется смена временного пароля.")
        return RedirectResponse("/password", status_code=303)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/password")
def password_page(request: Request, user: User = Depends(require_login)):
    return render(request, "password.html", user=user, forced=user.must_change_password)


@router.post("/password")
def password_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
    new_password: str = Form(...),
    new_password2: str = Form(...),
):
    if new_password != new_password2:
        flash(request, "Пароли не совпадают.")
        return render(request, "password.html", user=user, forced=user.must_change_password)
    try:
        users_service.change_password(
            db, user_id=user.id, new_password=new_password, actor_user_id=user.id
        )
    except ValueError as exc:
        flash(request, f"Ошибка: {exc}")
        return render(request, "password.html", user=user, forced=False)
    flash(request, "Пароль изменён.")
    return RedirectResponse("/", status_code=303)
