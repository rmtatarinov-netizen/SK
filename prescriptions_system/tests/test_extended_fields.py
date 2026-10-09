"""Расширенные поля предписания: справочные ссылки, обоснование, даты, фильтры реестра.

Покрывает несёт новые незакоммиченные изменения: PrescriptionCreate /
update_prescription_data (обоснование, issue_date, actual_fix_date,
enterprise/category/kind/mgmt/issuer), list_prescriptions с новыми
фильтрами и веб-маршруты new / {id}/data / реестр.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import select

from src.db import models as m
from src.db.models import (
    ConstructionManagement,
    Enterprise,
    Prescription,
    PrescriptionCategory,
    PrescriptionInspector,
    PrescriptionKind,
    PrescriptionStatus as PS,
    PrescriptionType as PT,
)
from src.db.repositories import list_prescriptions
from src.prescriptions.schemas import PrescriptionCreate
from src.prescriptions.service import create_prescription, update_prescription_data
from tests.conftest import login


def refs(db):
    """Справочники: категория, вид, управление, выдавший, предприятие."""
    cat = PrescriptionCategory(name="Категория А")
    kind = PrescriptionKind(name="Монолит")
    mgmt = ConstructionManagement(name="Иванов И.И.")
    issuer = PrescriptionInspector(name="Петров П.П.")
    ent = Enterprise(name="Завод Северный")
    db.add_all([cat, kind, mgmt, issuer, ent])
    db.commit()
    return cat, kind, mgmt, issuer, ent


# --- сервис: создание ----------------------------------------------------------


def test_create_prescription_with_extended_fields(db, obj, contractor, admin):
    cat, kind, mgmt, issuer, ent = refs(db)
    p = create_prescription(
        db,
        PrescriptionCreate(
            prescription_type=PT.INTERNAL,
            doc_number="EXT-1",
            doc_date=date(2026, 10, 1),
            issue_date=date(2026, 10, 2),
            actual_fix_date=date(2026, 10, 10),
            enterprise_id=ent.id,
            object_id=obj.id,
            contractor_id=contractor.id,
            description="Описание предписания",
            justification="Обоснование предписания",
            deadline=date(2026, 10, 15),
            category_id=cat.id,
            kind_id=kind.id,
            mgmt_id=mgmt.id,
            issuer_id=issuer.id,
        ),
        actor_user_id=admin.id,
    )
    assert p.issue_date == date(2026, 10, 2)
    assert p.actual_fix_date == date(2026, 10, 10)
    assert p.enterprise_id == ent.id
    assert p.justification == "Обоснование предписания"
    assert p.category_id == cat.id
    assert p.kind_id == kind.id
    assert p.mgmt_id == mgmt.id
    assert p.issuer_id == issuer.id


# --- сервис: правка ------------------------------------------------------------


def test_update_prescription_extended_fields(db, prescription, admin):
    cat, kind, mgmt, issuer, _ = refs(db)
    update_prescription_data(
        db,
        prescription,
        actor_user_id=admin.id,
        justification="Новое обоснование",
        category_id=cat.id,
        kind_id=kind.id,
        mgmt_id=mgmt.id,
        issuer_id=issuer.id,
    )
    db.refresh(prescription)
    assert prescription.justification == "Новое обоснование"
    assert prescription.category_id == cat.id
    assert prescription.kind_id == kind.id
    assert prescription.mgmt_id == mgmt.id
    assert prescription.issuer_id == issuer.id

    hist = db.scalar(
        select(m.PrescriptionHistory).where(
            m.PrescriptionHistory.prescription_id == prescription.id,
            m.PrescriptionHistory.change_type == "data_updated",
        )
    )
    assert hist is not None
    payload = json.loads(hist.new_value) if isinstance(hist.new_value, str) else hist.new_value
    assert set(payload) >= {"justification", "category_id", "kind_id", "mgmt_id", "issuer_id"}


def test_update_prescription_noop_does_not_log(db, prescription, admin):
    updated = update_prescription_data(db, prescription, actor_user_id=admin.id)
    db.expire_all()
    assert updated.justification is None
    assert db.query(m.PrescriptionHistory).count() == 0


def test_update_prescription_forbidden_for_contractor(db, prescription, contractor_user):
    with pytest.raises(PermissionError):
        update_prescription_data(
            db, prescription, actor_user_id=contractor_user.id, justification="нельзя"
        )


# --- репозиторий: новые фильтры --------------------------------------------------


def test_list_prescriptions_new_filters(db, obj, contractor):
    e1 = Enterprise(name="Порт Южный")
    e2 = Enterprise(name="Депо Восток")
    cat = PrescriptionCategory(name="Кат1")
    kind = PrescriptionKind(name="Вид1")
    db.add_all([e1, e2, cat, kind])
    db.commit()

    def mk(number, doc_dt, fix_dt, ent, category=None, k=None):
        p = Prescription(
            prescription_type=PT.CUSTOMER,
            doc_number=number,
            doc_date=doc_dt,
            object_id=obj.id,
            contractor_id=contractor.id,
            description="фильтр",
            status=PS.NEW,
            requires_review=False,
            enterprise_id=ent.id,
            category_id=category.id if category else None,
            kind_id=k.id if k else None,
            actual_fix_date=fix_dt,
        )
        db.add(p)
        db.commit()

    mk("FLT-1", date(2026, 1, 10), date(2026, 2, 20), e1, cat, kind)
    mk("FLT-2", date(2026, 3, 10), date(2026, 4, 20), e2, cat)
    mk("FLT-3", date(2026, 5, 10), None, e1)

    def numbers(**kw):
        return {p.doc_number for p in list_prescriptions(db, **kw)}

    assert numbers(enterprise_id=e1.id) == {"FLT-1", "FLT-3"}
    assert numbers(category_id=cat.id) == {"FLT-1", "FLT-2"}
    assert numbers(kind_id=kind.id) == {"FLT-1"}
    assert numbers(enterprise_id=e1.id, category_id=cat.id) == {"FLT-1"}
    assert numbers(date_issued_from=date(2026, 1, 1), date_issued_to=date(2026, 3, 15)) == {"FLT-1", "FLT-2"}
    assert numbers(date_fixed_from=date(2026, 2, 1), date_fixed_to=date(2026, 3, 1)) == {"FLT-1"}


# --- веб: форма создания ---------------------------------------------------------


def test_new_form_saves_extended_fields(db, client, persons, obj, contractor):
    cat, kind, mgmt, issuer, ent = refs(db)
    login(client, "web_insp")
    r = client.post(
        "/prescriptions/new",
        data={
            "prescription_type": "internal",
            "doc_number": "EXT-WEB-1",
            "doc_date": "2026-10-01",
            "issue_date": "2026-10-02",
            "enterprise_id": str(ent.id),
            "object_id": str(obj.id),
            "contractor_id": str(contractor.id),
            "responsible_user_id": str(persons.podr.id),
            "description": "Нет ограждения перепада",
            "justification": "Требование норм",
            "deadline": "2026-10-15",
            "category_id": str(cat.id),
            "kind_id": str(kind.id),
            "mgmt_id": str(mgmt.id),
            "issuer_id": str(issuer.id),
            "items_text": "Пункт 1\nПункт 2",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    p = db.scalar(select(Prescription).where(Prescription.doc_number == "EXT-WEB-1"))
    assert p is not None
    assert p.issue_date == date(2026, 10, 2)
    assert p.enterprise_id == ent.id
    assert p.responsible_user_id == persons.podr.id
    assert p.justification == "Требование норм"
    assert p.category_id == cat.id
    assert p.kind_id == kind.id
    assert p.mgmt_id == mgmt.id
    assert p.issuer_id == issuer.id
    assert len(p.items) == 2


def test_new_form_rejects_bad_issue_date(db, client, persons, obj, contractor):
    login(client, "web_insp")
    r = client.post(
        "/prescriptions/new",
        data={
            "prescription_type": "internal",
            "doc_number": "EXT-BAD",
            "doc_date": "2026-10-01",
            "issue_date": "02.10.2026",
            "object_id": str(obj.id),
            "contractor_id": str(contractor.id),
            "description": "bad date",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/prescriptions/new"
    db.expire_all()
    assert db.scalar(select(Prescription).where(Prescription.doc_number == "EXT-BAD")) is None


# --- веб: правка карточки --------------------------------------------------------


def test_card_data_update_extended_fields_web(db, client, persons, prescription):
    cat, kind, mgmt, issuer, _ = refs(db)
    login(client, "web_insp")
    r = client.post(
        f"/prescriptions/{prescription.id}/data",
        data={
            "description": prescription.description,
            "justification": "Обоснование с веб-формы",
            "deadline": "",
            "category_id": str(cat.id),
            "kind_id": str(kind.id),
            "mgmt_id": str(mgmt.id),
            "issuer_id": str(issuer.id),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(prescription)
    assert prescription.justification == "Обоснование с веб-формы"
    assert prescription.category_id == cat.id
    assert prescription.kind_id == kind.id
    assert prescription.mgmt_id == mgmt.id
    assert prescription.issuer_id == issuer.id


# --- веб: фильтры реестра --------------------------------------------------------


def test_registry_filters(db, client, persons, obj, contractor):
    cat, kind, _, _, ent = refs(db)
    create_prescription(
        db,
        PrescriptionCreate(
            prescription_type=PT.INTERNAL,
            doc_number="REG-INT",
            doc_date=date(2026, 10, 1),
            issue_date=date(2026, 10, 2),
            enterprise_id=ent.id,
            object_id=obj.id,
            contractor_id=contractor.id,
            description="внутреннее предписание",
            category_id=cat.id,
            kind_id=kind.id,
            responsible_user_id=persons.podr.id,
        ),
        actor_user_id=persons.insp.id,
    )
    create_prescription(
        db,
        PrescriptionCreate(
            prescription_type=PT.CUSTOMER,
            doc_number="REG-CUST",
            doc_date=date(2026, 1, 5),
            object_id=obj.id,
            contractor_id=contractor.id,
            description="предписание заказчика",
        ),
        actor_user_id=persons.insp.id,
    )
    login(client, "web_insp")

    base = client.get("/prescriptions").text
    assert "REG-INT" in base and "REG-CUST" in base

    def visible(query):
        return client.get(f"/prescriptions?{query}").text

    only_type = visible("prescription_type=internal")
    assert "REG-INT" in only_type and "REG-CUST" not in only_type
    only_ent = visible(f"enterprise_id={ent.id}")
    assert "REG-INT" in only_ent and "REG-CUST" not in only_ent
    by_cat_kind = visible(f"category_id={cat.id}&kind_id={kind.id}")
    assert "REG-INT" in by_cat_kind and "REG-CUST" not in by_cat_kind
    by_period = visible("issued_from=2026-09-01&issued_to=2026-10-31")
    assert "REG-INT" in by_period and "REG-CUST" not in by_period
    by_resp = visible("responsible_q=web_podr")
    assert "REG-INT" in by_resp and "REG-CUST" not in by_resp
    both_insp = visible("inspector_q=web_insp")
    assert "REG-INT" in both_insp and "REG-CUST" in both_insp
