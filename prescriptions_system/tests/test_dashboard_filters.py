"""Фильтры и метрики дашборда (Шаг 11 + незакоммиченные изменения):
get_period_dates, queries c filters=, build_dashboard_data, веб /reports.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.db import models as m
from src.db.models import (
    Enterprise,
    Prescription,
    PrescriptionHistory,
    PrescriptionStatus as PS,
    PrescriptionType as PT,
)
from src.reports import generator, queries
from tests.conftest import login

FULL_FROM = date(2000, 1, 1)
FULL_TO = date(2100, 1, 1)


def _mk_presc(db, obj, contractor, ent, number, doc_dt, status, deadline=None, closed_at=None, ptype=PT.INTERNAL):
    p = Prescription(
        prescription_type=ptype,
        doc_number=number,
        doc_date=doc_dt,
        enterprise_id=ent.id,
        object_id=obj.id,
        contractor_id=contractor.id,
        description="фильтр",
        status=status,
        requires_review=False,
        deadline=deadline,
        closed_at=closed_at,
    )
    db.add(p)
    db.commit()
    return p


def _seed(db, obj, contractor):
    e1 = Enterprise(name="Порт Северный")
    e2 = Enterprise(name="Депо Южное")
    db.add_all([e1, e2])
    db.commit()
    today = date.today()
    pa = _mk_presc(db, obj, contractor, e1, "DSH-CLOSED", today - timedelta(days=10), PS.CLOSED,
                   closed_at=datetime.combine(today, datetime.min.time()))
    pb = _mk_presc(db, obj, contractor, e1, "DSH-OPEN", today, PS.IN_WORK, deadline=today - timedelta(days=1))
    pc = _mk_presc(db, obj, contractor, e2, "DSH-CUST", today - timedelta(days=40), PS.NEW, ptype=PT.CUSTOMER)
    return SimpleNamespace(e1=e1, e2=e2, pa=pa, pb=pb, pc=pc)


# --- границы периода -------------------------------------------------------------


@pytest.mark.parametrize("kind, days", [("week", 7), ("month", 30), ("year", 365)])
def test_get_period_dates_deltas(kind, days):
    start, end = generator.get_period_dates(kind)
    assert (end - start).days == days


def test_get_period_dates_anchor_tuesday():
    today = date.today()
    expected_end = today - timedelta(days=(today.weekday() - 1) % 7)
    assert generator.get_period_dates("week")[1] == expected_end
    assert generator.get_period_dates("month")[1] == expected_end


def test_get_period_dates_custom_and_unknown():
    a, b = date(2026, 1, 1), date(2026, 2, 1)
    assert generator.get_period_dates("custom", a, b) == (a, b)
    assert generator.get_period_dates(None) == (None, None)
    assert generator.get_period_dates("decade") == (None, None)


# --- запросы с фильтрами -----------------------------------------------------------


def test_get_filtered_prescriptions(db, obj, contractor):
    seeded = _seed(db, obj, contractor)
    full = dict(date_from=FULL_FROM, date_to=FULL_TO)

    all_rows = {p.doc_number for p in queries.get_filtered_prescriptions(db, **full)}
    assert all_rows == {"DSH-CLOSED", "DSH-OPEN", "DSH-CUST"}
    assert [p.doc_number for p in
            queries.get_filtered_prescriptions(db, enterprise_id=seeded.e1.id, **full)] == ["DSH-CLOSED", "DSH-OPEN"]
    assert [p.doc_number for p in
            queries.get_filtered_prescriptions(db, prescription_type="customer", **full)] == ["DSH-CUST"]
    assert [p.doc_number for p in
            queries.get_filtered_prescriptions(db, date_from=FULL_FROM,
                                               date_to=date.today() - timedelta(days=15))] == ["DSH-CUST"]


def test_metric_queries_with_filters(db, obj, contractor):
    seeded = _seed(db, obj, contractor)
    f = dict(enterprise_id=seeded.e1.id, date_from=FULL_FROM, date_to=FULL_TO)

    assert queries.total_prescriptions(db, filters=f) == 2
    assert queries.status_breakdown(db, filters=f) == {"closed": 1, "in_work": 1}
    assert queries.type_breakdown(db, filters=f) == {"internal": 2}
    assert queries.overdue_count(db, filters=f) == 1
    assert queries.deadline_expired_count(db, filters=f) == 1
    assert queries.deadline_expired_count(db) == 1
    assert queries.average_fix_days(db, filters=f) == 10.0
    assert queries.by_contractor(db, filters=f) == [[ contractor.name, 2]]
    assert queries.by_object(db, filters=f) == [[obj.name, 2]]

    f2 = dict(enterprise_id=seeded.e2.id, date_from=FULL_FROM, date_to=FULL_TO)
    assert queries.total_prescriptions(db, filters=f2) == 1
    assert queries.overdue_count(db, filters=f2) == 0
    assert queries.average_fix_days(db, filters=f2) is None


def test_rework_count_with_filters(db, obj, contractor, admin):
    seeded = _seed(db, obj, contractor)
    db.add(PrescriptionHistory(
        prescription_id=seeded.pb.id,
        changed_by_user_id=admin.id,
        change_type="status_changed",
        old_value={"status": "fixed_pending_review"},
        new_value={"status": "in_work"},
    ))
    db.commit()
    f = dict(enterprise_id=seeded.e1.id, date_from=FULL_FROM, date_to=FULL_TO)
    assert queries.rework_count(db, filters=f) == 1
    assert queries.rework_count(db, filters={"enterprise_id": seeded.e2.id}) == 0


# --- дашборд ------------------------------------------------------------------------


def test_build_dashboard_data_new_metrics(db, obj, contractor):
    _seed(db, obj, contractor)
    data = generator.build_dashboard_data(db, filters={
        "period_type": "custom", "date_from": FULL_FROM, "date_to": FULL_TO,
    })
    assert data["total"] == 3
    assert data["fixed"] == 1
    assert data["not_fixed"] == 2
    assert data["overdue"] == 1
    assert data["deadline_expired"] == 1
    assert data["completion_pct"] == 33.3
    assert data["average_fix_days"] == 10.0
    for key in ("types", "statuses", "by_contractor", "by_object", "notifications"):
        assert key in data


def test_build_dashboard_data_enterprise_filter(db, obj, contractor):
    seeded = _seed(db, obj, contractor)
    data = generator.build_dashboard_data(db, filters={
        "period_type": "custom", "date_from": FULL_FROM, "date_to": FULL_TO,
        "enterprise_id": seeded.e2.id,
    })
    assert data["total"] == 1
    assert data["completion_pct"] == 0
    assert data["average_fix_days"] is None


def test_build_dashboard_data_without_filters_regression(db, obj, contractor):
    _seed(db, obj, contractor)
    data = generator.build_dashboard_data(db)
    assert data["total"] == 3
    assert data["overdue"] == 1


# --- веб -----------------------------------------------------------------------------


def test_reports_page_filters_via_http(db, client, persons, obj, contractor):
    seeded = _seed(db, obj, contractor)
    login(client, "web_adm")

    r = client.get("/reports?period_type=custom&date_from=2000-01-01&date_to=2100-01-01")
    assert r.status_code == 200
    assert '"total": 3' in r.text and '"fixed": 1' in r.text
    assert seeded.e1.name in r.text

    r = client.get(f"/reports?enterprise_id={seeded.e2.id}&period_type=custom"
                   f"&date_from=2000-01-01&date_to=2100-01-01")
    assert '"total": 1' in r.text

    r = client.get("/reports?period_type=custom&date_from=2100-01-01&date_to=2101-01-01")
    assert '"total": 0' in r.text

    r = client.get("/reports?period_type=custom&date_from=bad-date")
    assert r.status_code == 200
