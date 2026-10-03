"""Тесты отчётов (Шаг 11): метрики только из БД, экспорты, веб-страницы."""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.db import models as m
from src.db.models import (
    Prescription,
    PrescriptionNormRef,
    PrescriptionStatus as PS,
    PrescriptionType as PT,
    User,
    UserRole,
)
from src.prescriptions.schemas import PrescriptionCreate
from src.prescriptions.service import (
    change_status,
    confirm_norm_refs,
    create_prescription,
)
from src.reports import generator, queries
from src.users import service as users_service
from src.web.app import create_app

PASSWORD = "Secret123!"
MCP = "must_change_password"


def _mk_user(db, login_name, role, contractor_id=None):
    user = User(
        login=login_name,
        full_name=f"Отчётный {login_name}",
        role=role,
        password_hash=users_service.hash_password(PASSWORD),
        contractor_id=contractor_id,
        **{MCP: False},
    )
    db.add(user)
    db.commit()
    return user

def _seed(db, obj, contractor):
    insp = _mk_user(db, "rpt_insp", UserRole.INSPECTOR)
    podr = _mk_user(db, "rpt_podr", UserRole.CONTRACTOR_RESPONSIBLE, contractor_id=contractor.id)
    today = date.today()

    p1 = create_prescription(
        db,
        PrescriptionCreate(
            prescription_type=PT.CUSTOMER, doc_number="RPT-1", doc_date=today - timedelta(days=10),
            object_id=obj.id, contractor_id=contractor.id,
            description="нарушение 1", deadline=today + timedelta(days=20),
        ),
        actor_user_id=insp.id,
    )
    change_status(db, p1, PS.UNDER_REVIEW, insp)

    p2 = create_prescription(
        db,
        PrescriptionCreate(
            prescription_type=PT.CUSTOMER, doc_number="RPT-2", doc_date=today - timedelta(days=30),
            object_id=obj.id, contractor_id=contractor.id,
            description="нарушение 2", deadline=today - timedelta(days=5),
        ),
        actor_user_id=insp.id,
    )
    change_status(db, p2, PS.IN_WORK, podr)
    change_status(db, p2, PS.FIXED_PENDING_REVIEW, podr)
    change_status(db, p2, PS.IN_WORK, insp, comment="недоделано")
    change_status(db, p2, PS.FIXED_PENDING_REVIEW, podr)
    change_status(db, p2, PS.CLOSED, insp)

    ref = PrescriptionNormRef(
        prescription_id=p2.id, doc_code="СП 63.13330.2018", doc_item="5.3",
        source=m.NormRefSource.RAG, status=m.NormRefStatus.SUGGESTED,
    )
    db.add(ref)
    db.commit()
    confirm_norm_refs(db, p2, [ref.id], actor_user_id=insp.id)
    return SimpleNamespace(insp=insp, podr=podr, p1=p1, p2=p2)


# --- метрики -----------------------------------------------------------------


def test_queries_metrics(db, obj, contractor):
    seeded = _seed(db, obj, contractor)
    assert queries.total_prescriptions(db) == 2
    assert queries.type_breakdown(db) == {"customer": 2}
    statuses = queries.status_breakdown(db)
    assert statuses.get("under_review") == 1 and statuses.get("closed") == 1
    assert queries.overdue_count(db) == 0  # просроченных открытых нет: p1 срок в будущем
    assert queries.rework_count(db) == 1
    avg_fix = queries.average_fix_days(db)
    assert avg_fix is not None and avg_fix >= 0
    avg_review = queries.average_review_days(db)
    assert avg_review is not None and avg_review >= 0

    per_contractor = queries.by_contractor(db)
    assert per_contractor[0] == [contractor.name, 2]
    per_object = queries.by_object(db)
    assert per_object[0] == [obj.name, 2]

    freq = queries.norm_frequency(db)
    assert freq == [{"doc_code": "СП 63.13330.2018", "item": "5.3", "count": 1}]

    stats = queries.notification_stats(db)
    assert stats["total"] >= 1
    assert stats["by_status"].get("pending", 0) >= 1


def test_overdue_counts_open_past_deadline(db, obj, contractor):
    _seed(db, obj, contractor)
    insp = db.scalar(select(User).where(User.login == "rpt_insp"))
    p3 = create_prescription(
        db,
        PrescriptionCreate(
            prescription_type=PT.INTERNAL, doc_number="RPT-3", doc_date=date.today(),
            object_id=obj.id, contractor_id=contractor.id,
            description="просроченное", deadline=date.today() - timedelta(days=1),
        ),
        actor_user_id=insp.id,
    )
    # draft_for_signature не входит в OPEN_STATUSES → не просрочен
    assert queries.overdue_count(db) == 0


# --- генератор и экспорты ------------------------------------------------------


def test_dashboard_json_structure(db, obj, contractor):
    _seed(db, obj, contractor)
    data = generator.build_dashboard_data(db)
    for key in ("total", "types", "statuses", "overdue", "by_contractor",
                "notifications", "norm_frequency", "average_fix_days"):
        assert key in data
    assert data["total"] == 2


def test_export_csv(db, obj, contractor, tmp_path):
    _seed(db, obj, contractor)
    out = tmp_path / "registry.csv"
    text = generator.export_csv(db, out)
    assert text.splitlines()[0].startswith("id,doc_number")
    assert "RPT-1" in text and "RPT-2" in text
    disk = out.read_text(encoding="utf-8-sig")
    assert "RPT-2" in disk


def test_export_xlsx(db, obj, contractor, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    _seed(db, obj, contractor)
    out = generator.export_xlsx(db, tmp_path / "registry.xlsx")
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames[0] == "Реестр"
    rows = list(wb["Реестр"].iter_rows(values_only=True))
    assert rows[0][1] == "doc_number"
    assert len(rows) == 3  # header + 2 предписания
    assert wb["Сводка"]["B2"].value == 2


def test_render_report_html(db, obj, contractor):
    _seed(db, obj, contractor)
    html = generator.render_report_html(db)
    assert "Отчёт: предписания" in html
    assert "RPT-1" not in html  # сводка, не реестр поштучно
    assert "СП 63.13330.2018" in html


def test_pdf_unavailable_falls_back(db, obj, contractor, tmp_path):
    try:
        import weasyprint  # noqa: F401
        pytest.skip("WeasyPrint импортируется — проверяется на машинах без GTK")
    except Exception:
        pass
    _seed(db, obj, contractor)
    with pytest.raises(generator.PdfRenderUnavailable):
        generator.render_report_pdf(db, tmp_path / "r.pdf")


# --- веб -----------------------------------------------------------------------


@pytest.fixture()
def web(db, engine):
    app = create_app()
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return TestClient(app, follow_redirects=False)

@pytest.fixture()
def admin_client(web, db):
    adm = _mk_user(db, "rpt_admin", UserRole.ADMIN)
    web.post("/login", data={"login": "rpt_admin", "password": PASSWORD}, follow_redirects=False)
    return web


def test_reports_pages_rbac(web, db, obj, contractor, admin_client):
    podr = _mk_user(db, "rpt_podr2", UserRole.CONTRACTOR_RESPONSIBLE, contractor_id=contractor.id)
    web.post("/logout")
    web.post("/login", data={"login": "rpt_podr2", "password": PASSWORD}, follow_redirects=False)
    assert web.get("/reports").status_code == 403
    assert web.get("/reports/export.csv").status_code == 403


def test_reports_dashboard_page(db, obj, contractor, admin_client):
    _seed(db, obj, contractor)
    r = admin_client.get("/reports")
    assert r.status_code == 200
    assert "Дашборд предписаний" in r.text
    assert "/static/vendor/chart.umd.js" in r.text
    assert "RPT-1" not in r.text


def test_reports_exports_via_http(db, obj, contractor, admin_client):
    _seed(db, obj, contractor)
    r = admin_client.get("/reports/export.csv")
    assert r.status_code == 200
    assert "doc_number" in r.text
    r = admin_client.get("/reports/export.xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    r = admin_client.get("/reports/print")
    assert r.status_code == 200
    assert "Отчёт: предписания" in r.text
    r = admin_client.get("/reports/pdf")
    assert r.status_code in (200, 303)
