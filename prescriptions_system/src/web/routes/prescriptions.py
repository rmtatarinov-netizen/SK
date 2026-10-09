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
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    Contractor,
    Notification,
    Object,
    Enterprise,
    Prescription,
    PrescriptionAttachment,
    PrescriptionHistory,
    PrescriptionItem,
    PrescriptionNormRef,
    PrescriptionPrintVersion,
    PrescriptionStatus,
    PrescriptionType,
    PrescriptionCategory,
    PrescriptionKind,
    ConstructionManagement,
    PrescriptionInspector,
    User,
    UserRole,
)
from src.db.repositories import list_prescriptions
from src.llm.client import OllamaClient
from src.prescriptions import importer, print_service, signed_scan_service
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
    enterprise_id: str | None = None,
    status: str | None = None,
    object_id: str | None = None,
    contractor_id: str | None = None,
    category_id: str | None = None,
    kind_id: str | None = None,
    issued_from: str | None = None,
    issued_to: str | None = None,
    fixed_from: str | None = None,
    fixed_to: str | None = None,
    q: str | None = None,
    prescription_type: str | None = None,
    responsible_q: str | None = None,
    inspector_q: str | None = None,
    overdue: str | None = None,
    due_within: str | None = None,
    responsible_none: str | None = None,
    deadline_none: str | None = None,
    critical: str | None = None,
    ontime: str | None = None,
    closed_from: str | None = None,
    closed_to: str | None = None,
    include_drafts: str | None = None,
    section_id: str | None = None,
    work_type_id: str | None = None,
    mgmt_id: str | None = None,
    responsible_id: str | None = None,
):
    status_enum = None
    if status:
        try:
            status_enum = PrescriptionStatus(status)
        except ValueError:
            status_enum = None

    type_enum = None
    if prescription_type:
        try:
            type_enum = PrescriptionType(prescription_type)
        except ValueError:
            type_enum = None

    def _to_int(val):
        try:
            return int(val) if val and val.strip() else None
        except ValueError:
            return None

    def _to_date(val):
        try:
            return date.fromisoformat(val) if val and val.strip() else None
        except ValueError:
            return None

    items = list_prescriptions(
        db,
        status=status_enum,
        contractor_id=_to_int(contractor_id),
        object_id=_to_int(object_id),
        enterprise_id=_to_int(enterprise_id),
        category_id=_to_int(category_id),
        kind_id=_to_int(kind_id),
        date_issued_from=_to_date(issued_from),
        date_issued_to=_to_date(issued_to),
        date_fixed_from=_to_date(fixed_from),
        date_fixed_to=_to_date(fixed_to),
    )
    if user.role == UserRole.CONTRACTOR_RESPONSIBLE:
        items = [p for p in items if p.contractor_id == user.contractor_id]
    
    if q:
        needle = q.strip().lower()
        items = [p for p in items if needle in p.doc_number.lower()]
    
    if type_enum:
        items = [p for p in items if p.prescription_type == type_enum]
        
    if responsible_q:
        needle = responsible_q.strip().lower()
        items = [p for p in items if p.responsible_user and needle in p.responsible_user.full_name.lower()]
        
    if inspector_q:
        needle = inspector_q.strip().lower()
        items = [p for p in items if p.inspector_user and needle in p.inspector_user.full_name.lower()]

    today = date.today()

    def _closed(p):
        return p.actual_fix_date or (p.closed_at.date() if p.closed_at else None)

    if status == "open":
        items = [p for p in items if p.status in OPEN_STATUSES]
    if str(include_drafts or "") in ("0", "false"):
        items = [p for p in items if not p.requires_review]
    _section = _to_int(section_id)
    if _section:
        items = [p for p in items if p.section_id == _section]
    _work_type = _to_int(work_type_id)
    if _work_type:
        items = [p for p in items if p.work_type_id == _work_type]
    _mgmt = _to_int(mgmt_id)
    if _mgmt:
        items = [p for p in items if p.mgmt_id == _mgmt]
    _resp = _to_int(responsible_id)
    if _resp:
        items = [p for p in items if p.responsible_user_id == _resp]
    if responsible_none:
        items = [p for p in items if p.status in OPEN_STATUSES and p.responsible_user_id is None]
    if deadline_none:
        items = [p for p in items if p.status in OPEN_STATUSES and p.deadline is None]
    if overdue:
        items = [
            p for p in items
            if p.status in OPEN_STATUSES and p.deadline is not None and p.deadline < today
        ]
    _due_within = _to_int(due_within)
    if _due_within is not None:
        items = [
            p for p in items
            if p.status in OPEN_STATUSES and p.deadline is not None
            and 0 <= (p.deadline - today).days <= _due_within
        ]
    if critical:
        from src.dashboards.config import category_id_map

        a_id = category_id_map(db).get("a")
        items = [p for p in items if a_id is not None and p.category_id == a_id]
    _closed_from, _closed_to = _to_date(closed_from), _to_date(closed_to)
    if _closed_from or _closed_to:
        items = [
            p for p in items
            if p.status == PrescriptionStatus.CLOSED and _closed(p) is not None
            and (_closed_from is None or _closed(p) >= _closed_from)
            and (_closed_to is None or _closed(p) <= _closed_to)
        ]
    if ontime in ("ontime", "late"):
        def _on_time(p):
            cd = _closed(p)
            return (
                p.status == PrescriptionStatus.CLOSED
                and cd is not None and p.deadline is not None and cd <= p.deadline
            )
        items = [p for p in items if _on_time(p) is (ontime == "ontime")]

    rows = []
    for p in items:
        is_over = (
            p.deadline is not None
            and p.deadline < today
            and p.status in OPEN_STATUSES
        )
        rows.append(
            {
                "p": p,
                "overdue": is_over,
                "overdue_days": (today - p.deadline).days if is_over else 0,
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
        enterprises=db.scalars(select(Enterprise).order_by(Enterprise.name)).all(),
        categories=db.scalars(select(PrescriptionCategory).order_by(PrescriptionCategory.name)).all(),
        kinds=db.scalars(select(PrescriptionKind).order_by(PrescriptionKind.name)).all(),
        responsible_users=db.scalars(select(User).where(User.role == UserRole.CONTRACTOR_RESPONSIBLE).order_by(User.full_name)).all(),
        inspector_users=db.scalars(select(User).where(User.role == UserRole.INSPECTOR).order_by(User.full_name)).all(),
        filters={
            "status": status or "", 
            "q": q or "", 
            "object_id": object_id, 
            "contractor_id": contractor_id,
            "enterprise_id": enterprise_id or "",
            "category_id": category_id or "",
            "kind_id": kind_id or "",
            "issued_from": issued_from or "",
            "issued_to": issued_to or "",
            "fixed_from": fixed_from or "",
            "fixed_to": fixed_to or "",
            "prescription_type": prescription_type or "",
            "responsible_q": responsible_q or "",
            "inspector_q": inspector_q or "",
            "overdue": overdue or "",
            "due_within": due_within or "",
            "responsible_none": responsible_none or "",
            "deadline_none": deadline_none or "",
            "critical": critical or "",
            "ontime": ontime or "",
            "closed_from": closed_from or "",
            "closed_to": closed_to or "",
            "include_drafts": include_drafts if include_drafts is not None else "",
            "section_id": section_id or "",
            "work_type_id": work_type_id or "",
            "mgmt_id": mgmt_id or "",
            "responsible_id": responsible_id or "",
        },
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
        enterprises=db.scalars(select(Enterprise).order_by(Enterprise.name)).all(),
        objects=db.scalars(select(Object).where(Object.is_active.is_(True)).order_by(Object.name)).all(),
        contractors=db.scalars(select(Contractor).where(Contractor.is_active.is_(True)).order_by(Contractor.name)).all(),
        responsible_users=db.scalars(select(User).where(User.role == UserRole.CONTRACTOR_RESPONSIBLE, User.is_active.is_(True)).order_by(User.full_name)).all(),
        categories=db.scalars(select(PrescriptionCategory).order_by(PrescriptionCategory.name)).all(),
        kinds=db.scalars(select(PrescriptionKind).order_by(PrescriptionKind.name)).all(),
        mgmt=db.scalars(select(ConstructionManagement).order_by(ConstructionManagement.name)).all(),
        issuers=db.scalars(select(PrescriptionInspector).order_by(PrescriptionInspector.name)).all(),
    )


@router.post("/new")
def new_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
    prescription_type: str = Form(...),
    doc_number: str = Form(...),
    doc_date: str = Form(...),
    issue_date: str | None = Form(None),
    enterprise_id: str | None = Form(None),
    object_id: int = Form(...),
    contractor_id: int = Form(...),
    responsible_user_id: int | None = Form(None),
    description: str = Form(...),
    justification: str | None = Form(None),
    deadline: str | None = Form(None),
    category_id: int | None = Form(None),
    kind_id: int | None = Form(None),
    mgmt_id: int | None = Form(None),
    issuer_id: int | None = Form(None),
    items_text: str | None = Form(None),
):
    from src.prescriptions.schemas import PrescriptionCreate
    

    try:
        ptype = PrescriptionType(prescription_type)
        parsed_date = date.fromisoformat(doc_date)
        parsed_issue = date.fromisoformat(issue_date) if (issue_date or "").strip() else None
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
            issue_date=parsed_issue,
            enterprise_id=int(enterprise_id) if (enterprise_id or "").strip() else None,
            object_id=object_id,
            contractor_id=contractor_id,
            responsible_user_id=responsible_user_id,
            description=description.strip(),
            justification=justification.strip() if justification else None,
            deadline=parsed_deadline,
            category_id=category_id,
            kind_id=kind_id,
            mgmt_id=mgmt_id,
            issuer_id=issuer_id,
            items=items,
        )
        p = prescriptions_service.create_prescription(db, data, actor_user_id=user.id)
    except (ValueError, PermissionError) as exc:
        flash(request, f"Ошибка: {exc}")
        return RedirectResponse("/prescriptions/new", status_code=303)
    return RedirectResponse(f"/prescriptions/{p.id}", status_code=303)


@router.post("/import")
async def import_prescriptions_route(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
    file: UploadFile = File(...),
):
    try:
        payload = await file.read()
        rows = importer.read_table(payload, file.filename or "")
        result = importer.import_prescriptions(db, rows, actor_user_id=user.id)
    except (ValueError, PermissionError) as exc:
        flash(request, f"Ошибка импорта: {exc}")
        return RedirectResponse("/prescriptions", status_code=303)
    message = (
        f"Импорт: создано {len(result['created'])}, пропущено {len(result['skipped'])}, "
        f"ошибок {len(result['errors'])}."
    )
    if result["new_refs"]:
        message += f" Новые справочники: {', '.join(result['new_refs'])}."
    flash(request, message)
    if result["no_responsible"]:
        flash(request, "Не найдены ответственные: " + "; ".join(result["no_responsible"]))
    if result.get("no_inspector"):
        flash(request, "Инспекторы не найдены в Users: " + "; ".join(result["no_inspector"][:5]))
    if result.get("warnings"):
        flash(request, f"Предупреждения ({len(result['warnings'])}): " + "; ".join(result["warnings"][:3]))
    for err in result["errors"][:5]:
        flash(request, f"Строка отклонена: {err}")
    if len(result["errors"]) > 5:
        flash(request, f"…и ещё {len(result['errors']) - 5} ошибок.")
    return RedirectResponse("/prescriptions", status_code=303)


@router.get("/import/template.csv")
def import_template_route(
    user: User = Depends(require_role(UserRole.INSPECTOR, UserRole.ADMIN)),
):
    return Response(
        content=importer.template_csv().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="import_template.csv"'},
    )


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
        categories=db.scalars(select(PrescriptionCategory).order_by(PrescriptionCategory.name)).all(),
        kinds=db.scalars(select(PrescriptionKind).order_by(PrescriptionKind.name)).all(),
        mgmt=db.scalars(select(ConstructionManagement).order_by(ConstructionManagement.name)).all(),
        issuers=db.scalars(select(PrescriptionInspector).order_by(PrescriptionInspector.name)).all(),
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
    justification = form.get("justification")
    deadline_raw = (form.get("deadline") or "").strip()
    category_id = form.get("category_id")
    kind_id = form.get("kind_id")
    mgmt_id = form.get("mgmt_id")
    issuer_id = form.get("issuer_id")
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
            justification=justification.strip() if justification else None,
            deadline=parsed_deadline if deadline_raw else ...,
            category_id=int(category_id) if category_id and category_id.strip() else None,
            kind_id=int(kind_id) if kind_id and kind_id.strip() else None,
            mgmt_id=int(mgmt_id) if mgmt_id and mgmt_id.strip() else None,
            issuer_id=int(issuer_id) if issuer_id and issuer_id.strip() else None,
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
