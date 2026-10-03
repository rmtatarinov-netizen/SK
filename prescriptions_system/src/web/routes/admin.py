"""Администрирование: пользователи, роли, контакты, справочники (Шаг 9).

Только роль admin. Самостоятельная смена роли недоступна; смены ролей
логируются сервисом users.service. Временный пароль показывается один раз
после создания учётной записи.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Contractor, Object, RecipientChannel, User, UserRole
from src.notifications.enums import NotificationChannel
from src.users import service as users_service
from src.users.schemas import UserCreate
from src.web.deps import flash, get_db, render
from src.web.security import require_role

router = APIRouter(prefix="/admin", tags=["admin"])
require_admin = require_role(UserRole.ADMIN)


@router.get("/users")
def users_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    users = db.scalars(select(User).order_by(User.login)).all()
    channels = db.scalars(select(RecipientChannel).order_by(RecipientChannel.id)).all()
    return render(
        request,
        "admin/users.html",
        user=user,
        users=users,
        roles=[r.value for r in UserRole],
        contractors=db.scalars(select(Contractor).order_by(Contractor.name)).all(),
        objects=db.scalars(select(Object).order_by(Object.name)).all(),
        channels=channels,
        channel_values=[c.value for c in NotificationChannel],
    )


@router.post("/users")
def create_user_route(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    login: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(...),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    contractor_id: str | None = Form(None),
    object_id: str | None = Form(None),
    telegram_chat_id: str | None = Form(None),
):
    try:
        data = UserCreate(
            login=login.strip(),
            full_name=full_name.strip(),
            role=UserRole(role),
            email=(email or "").strip() or None,
            phone=(phone or "").strip() or None,
            contractor_id=int(contractor_id) if (contractor_id or "").strip() else None,
            object_id=int(object_id) if (object_id or "").strip() else None,
            telegram_chat_id=(telegram_chat_id or "").strip() or None,
        )
        created, temp_password = users_service.create_user(db, data, actor_user_id=user.id)
        flash(
            request,
            f"Пользователь {created.login} создан. Временный пароль (показать лично): {temp_password}",
        )
    except (ValueError, PermissionError) as exc:
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/role")
def change_role_route(
    request: Request,
    user_id: int,
    new_role: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        users_service.change_user_role(db, user_id, UserRole(new_role), user.id)
        flash(request, "Роль изменена.")
    except (ValueError, PermissionError) as exc:
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/deactivate")
def deactivate_route(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        users_service.deactivate_user(db, user_id, user.id)
        flash(request, "Пользователь заблокирован.")
    except (ValueError, PermissionError) as exc:
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/contacts")
def add_contact_route(
    request: Request,
    user_id: int,
    channel: str = Form(...),
    address: str = Form(...),
    is_preferred: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        users_service.set_recipient_channel(
            db,
            recipient_id=user_id,
            channel=NotificationChannel(channel),
            address=address.strip(),
            is_preferred=is_preferred,
        )
        db.commit()
        flash(request, "Контакт сохранён.")
    except ValueError as exc:
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/directories")
def directories_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    return render(
        request,
        "admin/directories.html",
        user=user,
        objects=db.scalars(select(Object).order_by(Object.name)).all(),
        contractors=db.scalars(select(Contractor).order_by(Contractor.name)).all(),
    )


@router.post("/directories/objects")
def add_object_route(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    name: str = Form(...),
    address: str | None = Form(None),
):
    name = name.strip()
    if db.scalar(select(Object).where(Object.name == name)):
        flash(request, "Объект с таким названием уже есть.")
    else:
        db.add(Object(name=name, address=(address or None)))
        db.commit()
        flash(request, "Объект добавлен.")
    return RedirectResponse("/admin/directories", status_code=303)


@router.post("/directories/contractors")
def add_contractor_route(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    name: str = Form(...),
    inn: str | None = Form(None),
):
    name = name.strip()
    if db.scalar(select(Contractor).where(Contractor.name == name)):
        flash(request, "Подрядчик с таким названием уже есть.")
    else:
        db.add(Contractor(name=name, inn=(inn or None)))
        db.commit()
        flash(request, "Подрядчик добавлен.")
    return RedirectResponse("/admin/directories", status_code=303)
