"""Отчёты и дашборд (Шаг 11): просмотрщик Chart.js, экспорт CSV/XLSX, печать/PDF."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from src.db.models import User, UserRole
from src.reports import generator, queries
from src.web.deps import flash, get_db, render
from src.web.security import require_role

router = APIRouter(prefix="/reports", tags=["reports"])

CAN_VIEW = require_role(UserRole.ADMIN, UserRole.INSPECTOR, UserRole.MANAGER)


@router.get("")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(CAN_VIEW),
):
    data = generator.build_dashboard_data(db)
    return render(
        request,
        "reports/dashboard.html",
        user=user,
        data=data,
        data_json=json.dumps(data, ensure_ascii=False),
    )


@router.get("/export.csv")
def export_csv_route(
    db: Session = Depends(get_db),
    user: User = Depends(CAN_VIEW),
):
    text = generator.export_csv(db)
    return Response(
        content=text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="prescriptions.csv"'},
    )


@router.get("/export.xlsx")
def export_xlsx_route(
    db: Session = Depends(get_db),
    user: User = Depends(CAN_VIEW),
):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp.close()
    path = generator.export_xlsx(db, tmp.name)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="prescriptions.xlsx",
        background=None,
    )


@router.get("/print")
def print_view(
    db: Session = Depends(get_db),
    user: User = Depends(CAN_VIEW),
):
    return HTMLResponse(generator.render_report_html(db))


@router.get("/pdf")
def pdf_route(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(CAN_VIEW),
):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    try:
        path = generator.render_report_pdf(db, tmp.name)
    except generator.PdfRenderUnavailable as exc:
        Path(tmp.name).unlink(missing_ok=True)
        flash(request, str(exc))
        return RedirectResponse("/reports/print", status_code=303)
    return FileResponse(path, media_type="application/pdf", filename="prescriptions_report.pdf")
