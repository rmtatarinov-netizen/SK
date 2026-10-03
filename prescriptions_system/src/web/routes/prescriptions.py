"""Реестр и карточка предписаний (Шаг 9). Контроллеры тонкие: все операции —
через сервисы (статусы, подтверждение регистрации/норм, вложения, печать,
подписной скан, outbox).

Подрядчик (contractor_responsible): просмотр своих, смена статуса, вложения.
Инспектор/администратор: правка данных, подтверждение OCR и норм, печать,
сформированная печатная версия и подписной скан внутреннего предписания.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    Contractor,
    Notification,
    Object,
    Prescription,
    PrescriptionAttachment,
    PrescriptionHistory,
    PrescriptionItem,
    PrescriptionNormRef,
    PrescriptionPrintVersion,
    PrescriptionStatus,
    PrescriptionType,
    User,
    UserRole,
)
from src.db.repositories import list_prescriptions
from src.llm.client import OllamaClient
from src.prescriptions import print_service, signed_scan_service
from src.prescriptions import service as prescriptions_service
from src.prescriptions import status_rules
from src.rag import recommend as rag_recommend
from src.rag.search import get_collection
from src.users.service import authenticate
from src.web.deps import flash, get_db, render
from src.web.security import require_login, require_role

router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])

OPEN_STATUSES = {
    PrescriptionStatus.NEW,
    PrescriptionStatus.UNDER_REVIEW,
    PrescriptionStatus.IN_WORK,
    PrescriptionStatus.FIXED_PENDING_REVIEW,
}


def _forbidden_detail(request: Request):
    return RedirectResponse("/login", status_code=303)


def _can_view(prescription: Prescription, user: User) -> bool:
    if user.role == UserRole.CONTRACTOR_RESPONSIBLE:
        return user.contractor_id is not None and user.contractor_id == prescription.contractor_id
    return True


def _allowed_transitions(prescription: Prescription, user: User) -> list[dict]:
    current = prescription.status
    if current in (PrescriptionStatus.CLOSED, PrescriptionStatus.REJECTED):
        if user.role != UserRole.ADMIN:
            return []
        targets = list(PrescriptionStatus)
    else:
        targets = [
            status
            for status, roles in status_rules.TRANSITIONS.get(current, {}).items()
            if user.role in roles
        ]
    out = []
    for status in targets:
        if status == current:
            continue
        out.append(
            {
                "value": status.value,
                "comment_required": (current, status) in status_rules.REQUIRED_COMMENT_TRANSITIONS,
            }
        )
    return out


@router.get("")
def registry(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
    status: str | None = None,
    object_id: int | None = None,
    contractor_id: int | None = None,
    q: str | None = None,
):
    status_enum = None
    if status:
        try:
            status_enum = PrescriptionStatus(status)
        except ValueError:
            status_enum = None

    items = list_prescriptions(
        db,
        status=status_enum,
        contractor_id=contractor_id,
        object_id=object_id,
    )
    if user.role == UserRole.CONTRACTOR_RESPONSIBLE:
        items = [p for p in items if p.contractor_id == user.contractor_id]
    if q:
        needle = q.strip().lower()
        items = [p for p in items if needle in p.doc_number.lower()]

    today = date.today()
    rows = []
    for p in items:
        rows.append(
            {
                "p": p,
                "overdue": p.deadline is not None
                and p.deadline < today
                and p.status in OPEN_STATUSES,
            }
        )
    rows.sort(key=lambda r: r["p"].created_at, reverse=True)
    return render(
        request,
        "prescriptions/list.html",
        user=user,
        rows=rows,
        statuses=list(PrescriptionStatus),
        objects=db.scalars(select(Object).order_by(Object.name)).all(),
        contractors=db.scalars(select(Contractor).order_by(Contractor.name)).all(),
        filters={"status": status or "", "q": q or "", "object_id": object_id, "contractor_id": contractor_id},
    )


@router.get("/new")
def new_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    return render(
        request,
        "prescriptions/new.html",
        user=user,
        objects=db.scalars(select(Object).where(Object.is_active.is_(True)).order_by(Object.name)).all(),
        contractors=db.scalars(select(Contractor).where(Contractor.is_active.is_(True)).order_by(Contractor.name)).all(),
    )


@router.post("/new")
def new_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
    prescription_type: str = Form(...),
    doc_number: str = Form(...),
    doc_date: str = Form(...),
    object_id: int = Form(...),
    contractor_id: int = Form(...),
    description: str = Form(...),
    deadline: str | None = Form(None),
    items_text: str | None = Form(None),
):
    from src.prescriptions.schemas import PrescriptionCreate

    try:
        ptype = PrescriptionType(prescription_type)
        parsed_date = date.fromisoformat(doc_date)
        parsed_deadline = date.fromisoformat(deadline) if deadline else None
        items = [
            {"text": line.strip()}
            for line in (items_text or "").splitlines()
            if line.strip()
        ]
        data = PrescriptionCreate(
            prescription_type=ptype,
            doc_number=doc_number.strip(),
            doc_date=parsed_date,
            object_id=object_id,
            contractor_id=contractor_id,
            description=description.strip(),
            deadline=parsed_deadline,
            items=items,
        )
        p = prescriptions_service.create_prescription(db, data, actor_user_id=user.id)
    except (ValueError, PermissionError) as exc:
        flash(request, f"Ошибка: {exc}")
        return RedirectResponse("/prescriptions/new", status_code=303)
    return RedirectResponse(f"/prescriptions/{p.id}", status_code=303)


@router.get("/{prescription_id}")
def card(
    request: Request,
    prescription_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None:
        flash(request, "Предписание не найдено.")
        return RedirectResponse("/prescriptions", status_code=303)
    if not _can_view(prescription, user):
        return _forbidden(request)

    history = db.scalars(
        select(PrescriptionHistory)
        .where(PrescriptionHistory.prescription_id == prescription.id)
        .order_by(PrescriptionHistory.created_at.desc(), PrescriptionHistory.id.desc())
    ).all()
    notifications = db.scalars(
        select(Notification)
        .where(Notification.prescription_id == prescription.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    ).all()
    attachments = db.scalars(
        select(PrescriptionAttachment)
        .where(PrescriptionAttachment.prescription_id == prescription.id)
        .order_by(PrescriptionAttachment.created_at)
    ).all()
    print_versions = db.scalars(
        select(PrescriptionPrintVersion)
        .where(PrescriptionPrintVersion.prescription_id == prescription.id)
        .order_by(PrescriptionPrintVersion.version_number)
    ).all()
    contractor_users = db.scalars(
        select(User).where(User.contractor_id == prescription.contractor_id, User.is_active.is_(True))
    ).all()
    is_overdue = (
        prescription.deadline is not None
        and prescription.deadline < date.today()
        and prescription.status in OPEN_STATUSES
    )
    return render(
        request,
        "prescriptions/card.html",
        user=user,
        p=prescription,
        history=history,
        notifications=notifications,
        attachments=attachments,
        print_versions=print_versions,
        transitions=_allowed_transitions(prescription, user),
        can_edit=user.role in (UserRole.INSPECTOR, UserRole.ADMIN),
        can_upload_scan=user.role in (UserRole.INSPECTOR, UserRole.ADMIN),
        can_add_attachment=(
            user.role in (UserRole.INSPECTOR, UserRole.ADMIN)
            or (
                user.role == UserRole.CONTRACTOR_RESPONSIBLE
                and prescription.status in OPEN_STATUSES
                and prescription.contractor_id == user.contractor_id
            )
        ),
        contractor_users=contractor_users,
        is_overdue=is_overdue,
    )


def _forbidden(request: Request):
    raise HTTPException(status_code=403, detail="Доступ запрещён")


@router.post("/{prescription_id}/status")
def change_status_route(
    request: Request,
    prescription_id: int,
    new_status: str = Form(...),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None:
        return RedirectResponse("/prescriptions", status_code=303)
    try:
        target = PrescriptionStatus(new_status)
        prescriptions_service.change_status(
            db, prescription, target, user, comment=(comment or None)
        )
        flash(request, "Статус изменён.")
    except (ValueError, PermissionError) as exc:
        db.rollback()
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)


@router.post("/{prescription_id}/data")
async def update_data_route(
    request: Request,
    prescription_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None:
        return RedirectResponse("/prescriptions", status_code=303)
    form = await request.form()
    description = form.get("description")
    deadline_raw = (form.get("deadline") or "").strip()
    item_updates: dict[int, str] = {}
    for key, value in form.items():
        if key.startswith("item_"):
            try:
                item_updates[int(key[5:])] = str(value)
            except ValueError:
                continue
    parsed_deadline = date.fromisoformat(deadline_raw) if deadline_raw else None
    try:
        prescriptions_service.update_prescription_data(
            db,
            prescription,
            actor_user_id=user.id,
            description=description if description is not None else None,
            deadline=parsed_deadline if deadline_raw else ...,
            item_updates=item_updates or None,
        )
        flash(request, "Данные обновлены.")
    except (ValueError, PermissionError) as exc:
        db.rollback()
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)


@router.post("/{prescription_id}/confirm")
def confirm_route(
    request: Request,
    prescription_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None:
        return RedirectResponse("/prescriptions", status_code=303)
    try:
        prescriptions_service.confirm_registration(db, prescription, actor_user_id=user.id)
        flash(request, "Регистрация подтверждена, Подрядчик уведомляется.")
    except (ValueError, PermissionError) as exc:
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)


@router.post("/{prescription_id}/norms/confirm")
def confirm_norms_route(
    request: Request,
    prescription_id: int,
    ref_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None:
        return RedirectResponse("/prescriptions", status_code=303)
    try:
        count = prescriptions_service.confirm_norm_refs(
            db, prescription, list(ref_ids), actor_user_id=user.id
        )
        flash(request, f"Подтверждено нормативных ссылок: {count}.")
    except (ValueError, PermissionError) as exc:
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)


@router.post("/{prescription_id}/items/{item_id}/suggest-norms")
def suggest_norms_route(
    request: Request,
    prescription_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    prescription = db.get(Prescription, prescription_id)
    item = db.get(PrescriptionItem, item_id)
    if prescription is None or item is None or item.prescription_id != prescription.id:
        return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)
    try:
        client = OllamaClient()
        collection = get_collection()
        proposals = rag_recommend.recommend_for_text(
            client, collection, item.text, db=db, prescription_id=prescription.id
        )
        saved = rag_recommend.save_proposed_refs(
            db, prescription, proposals, item_id=item.id
        )
        flash(request, f"RAG предложил ссылок: {len(proosals)}, новых: {saved}.")
    except Exception as exc:
        db.rollback()
        flash(request, f"RAG/LLM недоступен: {exc}")
    return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)


@router.post("/{prescription_id}/attachments")
async def add_attachment_route(
    request: Request,
    prescription_id: int,
    file: UploadFile = File(...),
    caption: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None or not _can_view(prescription, user):
        return RedirectResponse("/prescriptions", status_code=303)
    suffix = Path(file.filename or "upload.bin").suffix.lower() or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()
        attachment = prescriptions_service.add_attachment(
            db, prescription, source_path=tmp.name, actor_user_id=user.id,
            caption=(caption or file.filename),
        )
        flash(request, "Вложение добавлено." if attachment else "Вложение уже было (дубликат).")
    except (ValueError, PermissionError) as exc:
        db.rollback()
        flash(request, f"Ошибка: {exc}")
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass
    return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)


@router.post("/{prescription_id}/print-version")
def generate_print_version_route(
    request: Request,
    prescription_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None:
        return RedirectResponse("/prescriptions", status_code=303)
    try:
        version = print_service.generate_print_version(
            db, prescription, actor_user_id=user.id
        )
        flash(request, f"Печатная версия v{version.version_number} сформирована.")
    except (ValueError, PermissionError) as exc:
        db.rollback()
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)


@router.get("/{prescription_id}/print")
def print_route(
    request: Request,
    prescription_id: int,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None:
        return RedirectResponse("/prescriptions", status_code=303)
    try:
        version = print_service.request_print(
            db, prescription, actor_user_id=user.id, version_id=version_id
        )
    except ValueError as exc:
        flash(request, f"Ошибка: {exc}")
        return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)
    return FileResponse(
        version.file_path,
        media_type="application/pdf",
        filename=f"prescription_{prescription.id}_v{version.version_number}.pdf",
    )


@router.post("/{prescription_id}/signed-scan")
async def upload_signed_scan_route(
    request: Request,
    prescription_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None:
        return RedirectResponse("/prescriptions", status_code=303)
    suffix = Path(file.filename or "scan.pdf").suffix.lower() or ".pdf"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()
        attachment = signed_scan_service.link_signed_scan(
            db, prescription, tmp.name, actor_user_id=user.id
        )
        already = attachment.uploaded_by_user_id != user.id
        flash(request, "Подписной скан привязан." if not already else "Такой скан уже был привязан (без дубля).")
    except (ValueError, PermissionError) as exc:
        db.rollback()
        flash(request, f"Ошибка: {exc}")
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass
    return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)


@router.post("/{prescription_id}/verify-scan")
def verify_scan_route(
    request: Request,
    prescription_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    prescription = db.get(Prescription, prescription_id)
    if prescription is None:
        return RedirectResponse("/prescriptions", status_code=303)
    try:
        signed_scan_service.verify_signed_scan(
            db, prescription, actor_user_id=user.id
        )
        flash(request, "Скан проверен. Можно зарегистрировать предписание.")
    except (ValueError, PermissionError) as exc:
        flash(request, f"Ошибка: {exc}")
    return RedirectResponse(f"/prescriptions/{prescription_id}", status_code=303)
