"""Дашборд 01 «Панель руководителя»: KPI-сходимость, риск-балл, drill-down, права.

Граничные условия по ТЗ (docs/dashboards/01, 00 §4): deadline==today не просрочен;
fix==deadline — в срок; черновики requires_review исключены из KPI.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.db import models as m
from src.dashboards import metrics
from src.dashboards.config import DEFAULT_RISK_WEIGHTS, category_id_map
from tests.conftest import login

TODAY = date.today()


def _cat(db, name):
    cat = db.query(m.PrescriptionCategory).filter_by(name=name).first()
    if cat is None:
        cat = m.PrescriptionCategory(name=name)
        db.add(cat)
        db.commit()
    return cat


def _presc(db, obj, contractor, number, *, status, deadline=None, fix=None,
           category=None, doc_delta=10, responsible=None, draft=False,
           work_type=None):
    p = m.Prescription(
        prescription_type=m.PrescriptionType.CUSTOMER,
        doc_number=number,
        doc_date=TODAY - timedelta(days=doc_delta),
        object_id=obj.id,
        contractor_id=contractor.id,
        description=f"Замечание {number}",
        deadline=deadline,
        actual_fix_date=fix,
        status=status,
        category_id=category.id if category else None,
        work_type_id=work_type.id if work_type else None,
        responsible_user_id=responsible.id if responsible else None,
        requires_review=draft,
    )
    if status == m.PrescriptionStatus.CLOSED:
        p.closed_at = __import__("datetime").datetime.combine(
            fix or (TODAY - timedelta(days=1)), __import__("datetime").time.min
        )
    db.add(p)
    db.commit()
    return p


@pytest.fixture()
def seeded(db, obj, contractor):
    a = _cat(db, "А")
    b = _cat(db, "В")
    wt = m.WorkType(name="Монолитные работы", risk_weight=8)
    db.add(wt)
    db.commit()
    resp = m.User(login="resp1", full_name="Иванов И.И.",
                  role=m.UserRole.CONTRACTOR_RESPONSIBLE, password_hash="x")
    db.add(resp)
    db.commit()
    over_a = _presc(db, obj, contractor, "OVER-A", status=m.PrescriptionStatus.IN_WORK,
                    deadline=TODAY - timedelta(days=20), category=a, responsible=resp,
                    work_type=wt)
    today_due = _presc(db, obj, contractor, "DUE-TODAY", status=m.PrescriptionStatus.NEW,
                       deadline=TODAY, category=b, responsible=resp)
    due3 = _presc(db, obj, contractor, "DUE-3", status=m.PrescriptionStatus.IN_WORK,
                  deadline=TODAY + timedelta(days=3), category=b)
    closed_ontime = _presc(db, obj, contractor, "FIX-OK", status=m.PrescriptionStatus.CLOSED,
                           deadline=TODAY - timedelta(days=2), fix=TODAY - timedelta(days=2),
                           category=b)
    closed_late = _presc(db, obj, contractor, "FIX-LATE", status=m.PrescriptionStatus.CLOSED,
                         deadline=TODAY - timedelta(days=5), fix=TODAY - timedelta(days=1),
                         category=b)
    draft = _presc(db, obj, contractor, "DRAFT", status=m.PrescriptionStatus.IN_WORK,
                   deadline=TODAY - timedelta(days=3), category=a, draft=True)
    return {
        "a": a, "b": b, "wt": wt, "resp": resp,
        "over_a": over_a, "today_due": today_due, "due3": due3,
        "closed_ontime": closed_ontime, "closed_late": closed_late, "draft": draft,
    }


# --- предикаты -------------------------------------------------------------

def test_overdue_boundary(db, seeded):
    row = {"status": "new", "deadline": TODAY, "actual_fix_date": None, "closed_at": None}
    assert not metrics.is_overdue(row, TODAY)          # deadline == today → не просрочено
    row["deadline"] = TODAY - timedelta(days=1)
    assert metrics.is_overdue(row, TODAY)


def test_predicates_units():
    row_open = {"status": "in_work", "deadline": TODAY - timedelta(days=1),
                "actual_fix_date": None, "closed_at": None}
    assert metrics.is_open(row_open) and metrics.is_overdue(row_open, TODAY)
    assert metrics.days_overdue(row_open, TODAY) == 1
    row_today = dict(row_open, deadline=TODAY)
    assert not metrics.is_overdue(row_today, TODAY)
    assert metrics.due_in_days(row_today, TODAY) == 0
    row_fix = {"status": "closed", "deadline": TODAY - timedelta(days=2),
               "actual_fix_date": TODAY - timedelta(days=2), "closed_at": None}
    assert metrics.fixed_on_time(row_fix)          # fix == deadline — в срок
    row_late = dict(row_fix, actual_fix_date=TODAY - timedelta(days=1))
    assert not metrics.fixed_on_time(row_late)


def test_risk_score_weights():
    weights = dict(DEFAULT_RISK_WEIGHTS)
    grades = {1: "a"}
    base = {"category_id": 1, "deadline": TODAY - timedelta(days=15),
            "actual_fix_date": None, "closed_at": None, "responsible_id": 5,
            "recurrence_of_id": None, "has_confirmed_ref": True, "status": "in_work"}
    score = metrics.risk_score(base, weights, grades, TODAY)
    assert score == 50 + 30                                   # А + просрочка > 14
    assert metrics.risk_band(score) == "critical"
    no_resp = dict(base, responsible_id=None, deadline=None,
                   has_confirmed_ref=False, recurrence_of_id=9)
    assert metrics.risk_score(no_resp, weights, grades, TODAY) == 50 + 20 + 15 + 15 + 10
    low = {"category_id": None, "deadline": TODAY + timedelta(days=30),
           "actual_fix_date": None, "closed_at": None, "responsible_id": 1,
           "recurrence_of_id": None, "has_confirmed_ref": True, "status": "in_work"}
    assert metrics.risk_band(metrics.risk_score(low, weights, grades, TODAY)) == "controlled"


# --- KPI на срезе БД ---------------------------------------------------------

def _all(params=None, **kwargs):
    p = {"period_type": "all"}
    p.update(params or {})
    p.update(kwargs)
    return p


def test_kpis_and_draft_exclusion(db, obj, contractor, seeded):
    data = metrics.build_executive(db, _all())
    k = data["kpis"]
    assert k["open"] == 3                                  # DRAFT исключён
    assert k["overdue"] == 1
    assert k["due_7"] == 2
    assert k["fixed"] == 2
    assert k["on_time_pct"] == 50.0
    assert k["critical_open"] == 1                         # OVER-A
    assert k["avg_fix_days"] is not None
    assert data["top10"][0]["doc_number"] == "OVER-A"
    assert data["top10"][0]["band"] == "critical"
    assert "critical=1" in data["kpi_cards"][1]["href"]


def test_category_structure_and_objects(db, obj, contractor, seeded):
    data = metrics.build_executive(db, _all())
    cats = {row[0]: row[1] for row in data["structure_category"]}
    assert cats["В"] == 4
    assert data["objects"][0]["object"] == obj.name
    assert data["objects"][0]["overdue"] == 1
    assert data["objects"][0]["critical"] == 1


def test_need_decision_rules(db, obj, contractor, seeded):
    data = metrics.build_executive(db, _all())
    reasons = [n["reason"] for n in data["need_decision"]]
    assert any("просрочена на 20" in r for r in reasons)
    ids = {n["id"] for n in data["need_decision"]}
    assert seeded["over_a"].id in ids


def test_dynamics_buckets_converge(db, obj, contractor, seeded):
    data = metrics.build_executive(db, _all())
    buckets = data["dynamics"]["buckets"]
    total_issued = sum(b["issued"] for b in buckets)
    assert total_issued == 5                               # 5 не-черновиков
    total_fixed = sum(b["fixed"] for b in buckets)
    assert total_fixed == 2


# --- веб: экран, drill-down, права -------------------------------------------

def test_executive_page_ok(db, client, persons, seeded):
    login(client, "web_adm")
    r = client.get("/dash/executive?period_type=all")
    assert r.status_code == 200
    assert "Открытые предписания" in r.text
    assert "OVER-A" in r.text


def test_executive_json(db, client, persons, seeded):
    login(client, "web_adm")
    r = client.get("/dash/executive/data?period_type=all")
    assert r.status_code == 200
    assert r.json()["kpis"]["open"] == 3


def test_drill_down_matches_kpi(db, client, persons, seeded):
    login(client, "web_adm")
    data = metrics.build_executive(db, _all())
    import re
    for card, expected in zip(data["kpi_cards"], [3, 1, 1, 2, 2, 1, 2, 3]):
        r = client.get(card["href"])
        assert r.status_code == 200, card["href"]
        ids = set(re.findall(r'href="/prescriptions/(\d+)"', r.text))
        assert len(ids) == expected, (card["key"], len(ids), expected)


def test_registry_new_filters(db, client, persons, seeded):
    login(client, "web_adm")
    r = client.get("/prescriptions?status=open&overdue=1&issued_from=2000-01-01"
                   "&issued_to=2100-01-01&include_drafts=0")
    assert "OVER-A" in r.text
    assert "DRAFT" not in r.text
    r = client.get("/prescriptions?status=open&responsible_none=1"
                   "&issued_from=2000-01-01&issued_to=2100-01-01&include_drafts=0")
    assert "DUE-3" in r.text and "OVER-A" not in r.text
    r = client.get("/prescriptions?status=closed&ontime=late"
                   "&closed_from=2000-01-01&closed_to=2100-01-01&include_drafts=0")
    assert "FIX-LATE" in r.text and "FIX-OK" not in r.text


def test_drill_with_filters(db, client, persons, seeded):
    """Сходимость KPI↔реестр при активном срезе (responsible, work_type)."""
    import re
    login(client, "web_adm")
    data = metrics.build_executive(
        db, _all(responsible_id=str(seeded["resp"].id))
    )
    assert data["kpis"]["open"] == 2          # OVER-A + DUE-TODAY
    href = data["kpi_cards"][0]["href"]
    assert "responsible_id" in href and "overdue" not in href
    ids = set(re.findall(r'href="/prescriptions/(\d+)"', client.get(href).text))
    assert len(ids) == 2
    ids_in_page = set(re.findall(r'href="/prescriptions/(\d+)"',
                                 client.get(f"/prescriptions?status=open"
                                            f"&responsible_id={seeded['resp'].id}"
                                            f"&include_drafts=0").text))
    assert len(ids_in_page) == 2

    data_wt = metrics.build_executive(db, _all(work_type_id=str(seeded["wt"].id)))
    assert data_wt["kpis"]["open"] == 1
    ids_wt = set(re.findall(r'href="/prescriptions/(\d+)"',
                            client.get(data_wt["kpi_cards"][0]["href"]).text))
    assert len(ids_wt) == 1


def test_contractor_role_forbidden(db, client, persons, seeded):
    login(client, "web_podr")
    r = client.get("/dash/executive")
    assert r.status_code == 403


def test_anonymous_redirected(db, client, seeded):
    r = client.get("/dash/executive")
    assert r.status_code in (303, 307)
