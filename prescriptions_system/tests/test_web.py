"""Тесты веб-интерфейса (Шаг 9): auth, RBAC, статусы, нормы, файлы, печать.

БД — in-memory SQLite (тот же engine, что и фикстура db) через
app.state.session_factory; TestClient, сеть не используется.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from sqlalchemy import select

from src.db import models as m
from src.db.models import (
    Notification,
    Prescription,
    PrescriptionAttachment,
    PrescriptionHistory,
    PrescriptionItem,
    PrescriptionNormRef,
    PrescriptionPrintVersion,
    PrescriptionStatus as PS,
    PrescriptionType as PT,
    RecipientChannel,
    User,
    UserRole,
    UserRoleChangeLog,
)
from src.notifications.enums import NotificationChannel, NotificationEvent
from src.prescriptions.schemas import PrescriptionCreate
from src.prescriptions.service import change_status, create_prescription
from src.rag.recommend import ProposedNormRef
from src.users import service as users_service
from src.users.schemas import UserCreate
from src.web.routes import prescriptions as pr_routes
from tests.conftest import _mk_user, login

MCP = "must_change_password"


# --- auth --------------------------------------------------------------------


def test_anonymous_redirected_to_login(client):
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/prescriptions"
    r2 = client.get("/prescriptions")
    assert r2.status_code == 303 and r2.headers["location"] == "/login"


def test_login_wrong_and_right(client, persons):
    r = client.post("/login", data={"login": "web_insp", "password": "bad"})
    assert r.status_code == 200 and "Неверный" in r.text
    assert login(client, "web_insp").status_code == 303
    assert client.get("/prescriptions").status_code == 200


def test_temp_password_flow(db, client, persons):
    created, temp = users_service.create_user(
        db,
        UserCreate(login="temp_user", full_name="Временный Тест", role=UserRole.MANAGER),
        actor_user_id=persons.adm.id,
    )
    assert getattr(created, MCP) is True
    r = client.post(
        "/login",
        data={"login": "temp_user", "password": temp},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/password"
    blocked = client.get("/prescriptions")
    assert blocked.status_code == 303 and blocked.headers["location"] == "/password"
    r = client.post(
        "/password",
        data={"new_password": "NewStrong123", "new_password2": "NewStrong123"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(created)
    assert getattr(created, MCP) is False
    assert client.get("/prescriptions").status_code == 200


def test_logout(client, persons):
    login(client, "web_insp")
    assert client.post("/logout", follow_redirects=False).status_code == 303
    assert client.get("/prescriptions").status_code == 303


# --- реестр и карточка -------------------------------------------------------


def test_registry_scoping_and_card_403(db, client, persons, obj, contractor):
    own = Prescription(
        prescription_type=PT.CUSTOMER, doc_number="WEB-OWN-1", doc_date=date(2026, 3, 1),
        object_id=obj.id, contractor_id=contractor.id, description="своё предписание",
        status=PS.NEW, requires_review=True,
    )
    db.add(own)
    other_org = m.Contractor(name="WEB Чужой Подряд")
    db.add(other_org)
    db.commit()
    foreign = Prescription(
        prescription_type=PT.CUSTOMER, doc_number="WEB-FOREIGN-1", doc_date=date(2026, 3, 2),
        object_id=obj.id, contractor_id=other_org.id, description="чужое предписание",
        status=PS.NEW, requires_review=True,
    )
    db.add(foreign)
    db.commit()

    login(client, "web_podr")
    text = client.get("/prescriptions").text
    assert "WEB-OWN-1" in text and "WEB-FOREIGN-1" not in text
    assert client.get(f"/prescriptions/{foreign.id}").status_code == 403

    client.post("/logout")
    login(client, "web_insp")
    assert "WEB-FOREIGN-1" in client.get("/prescriptions").text


def test_status_change_via_web(db, client, persons, prescription):
    login(client, "web_podr")
    r = client.post(
        f"/prescriptions/{prescription.id}/status",
        data={"new_status": "in_work", "comment": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(prescription)
    assert prescription.status == PS.IN_WORK
    notes = db.scalars(
        select(Notification).where(
            Notification.prescription_id == prescription.id,
            Notification.event_type == NotificationEvent.STATUS_CHANGED_TO_IN_WORK,
        )
    ).all()
    assert notes


def test_status_return_requires_comment_via_web(
    db, client, persons, contractor_user, prescription
):
    change_status(db, prescription, PS.IN_WORK, contractor_user)
    change_status(db, prescription, PS.FIXED_PENDING_REVIEW, contractor_user)
    login(client, "web_insp")
    client.post(
        f"/prescriptions/{prescription.id}/status",
        data={"new_status": "in_work", "comment": ""},
        follow_redirects=False,
    )
    db.refresh(prescription)
    assert prescription.status == PS.FIXED_PENDING_REVIEW
    client.post(
        f"/prescriptions/{prescription.id}/status",
        data={"new_status": "in_work", "comment": "недоделано"},
        follow_redirects=False,
    )
    db.refresh(prescription)
    assert prescription.status == PS.IN_WORK


def test_confirm_registration_via_web(db, client, persons, prescription):
    login(client, "web_insp")
    r = client.post(
        f"/prescriptions/{prescription.id}/confirm", follow_redirects=False
    )
    assert r.status_code == 303
    db.refresh(prescription)
    assert prescription.confirmed_at is not None
    assert prescription.requires_review is False
    created = db.scalars(
        select(Notification).where(
            Notification.prescription_id == prescription.id,
            Notification.event_type == NotificationEvent.PRESCRIPTION_CREATED,
        )
    ).all()
    assert len(created) >= 2


def test_confirm_data_via_web(db, client, persons, prescription):
    login(client, "web_insp")
    r = client.post(
        f"/prescriptions/{prescription.id}/data",
        data={
            "description": "Описание отредактировано инспектором",
            "deadline": "2026-11-11",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(prescription)
    assert prescription.description.startswith("Описание отредактировано")
    assert prescription.deadline == date(2026, 11, 11)


def test_contractor_cannot_edit_data(db, client, persons, prescription):
    login(client, "web_podr")
    r = client.post(
        f"/prescriptions/{prescription.id}/data",
        data={"description": "попытка подрядчика менять данные"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    db.expire_all()
    assert not prescription.description.startswith("попытка")


def test_norm_refs_confirm_via_web(db, client, persons, prescription):
    ref = PrescriptionNormRef(
        prescription_id=prescription.id, doc_code="СП 1.2.3.4",
        source=m.NormRefSource.LLM, status=m.NormRefStatus.SUGGESTED,
    )
    db.add(ref)
    db.commit()
    login(client, "web_insp")
    r = client.post(
        f"/prescriptions/{prescription.id}/norms/confirm",
        data={"ref_ids": str(ref.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(ref)
    assert ref.status == m.NormRefStatus.CONFIRMED
    assert ref.confirmed_by_user_id == persons.insp.id


def test_suggest_norms_rag_route(db, client, persons, prescription, monkeypatch):
    item = PrescriptionItem(prescription_id=prescription.id, item_number=1, text="Устранить")
    db.add(item)
    db.commit()
    fake_rec = [
        ProposedNormRef(
            doc_code="СП 63.13330.2018", item="5.3", fragment="фрагмент",
            chunk_id=None, reason="r", distance=None,
        )
    ]
    justification = "1. СП 63.13330.2018, п. 5.3 — r\n   Фрагмент: «фрагмент»"
    from src.rag import recommend as rr

    # Маршрут вызывает generate_justification — мок должен повторять
    # реальную сигнатуру (регрессия: раньше мок скрывал TypeError).
    monkeypatch.setattr(
        pr_routes,
        "rag_recommend",
        SimpleNamespace(
            generate_justification=lambda *a, **k: (fake_rec, justification),
            save_proposed_refs=rr.save_proposed_refs,
            build_justification=rr.build_justification,
        ),
    )
    login(client, "web_insp")
    r = client.post(
        f"/prescriptions/{prescription.id}/items/{item.id}/suggest-norms",
        follow_redirects=False,
    )
    assert r.status_code == 303
    saved = db.scalars(
        select(PrescriptionNormRef).where(
            PrescriptionNormRef.prescription_id == prescription.id,
            PrescriptionNormRef.status == m.NormRefStatus.SUGGESTED,
        )
    ).all()
    assert [s.doc_code for s in saved] == ["СП 63.13330.2018"]
    db.refresh(item)
    assert item.justification == justification


def test_attachment_upload_and_dedup(
    db, client, persons, contractor_user, prescription, tmp_storage
):
    change_status(db, prescription, PS.IN_WORK, contractor_user)
    login(client, "web_podr")
    payload = {"file": ("a.jpg", b"same-bytes", "image/jpeg")}
    r = client.post(
        f"/prescriptions/{prescription.id}/attachments",
        files=payload,
        data={"caption": "фото устранения"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r2 = client.post(
        f"/prescriptions/{prescription.id}/attachments",
        files=payload,
        follow_redirects=False,
    )
    assert r2.status_code == 303
    atts = db.scalars(
        select(PrescriptionAttachment).where(
            PrescriptionAttachment.prescription_id == prescription.id
        )
    ).all()
    assert len(atts) == 1
    assert atts[0].uploaded_by_user_id == persons.podr.id


def test_attachment_forbidden_for_foreign_contractor(db, client, persons, contractor, prescription):
    other_org = m.Contractor(name="WEB Чужой Подряд 2")
    db.add(other_org)
    db.commit()
    other_user = _mk_user(
        db, "web_other2", UserRole.CONTRACTOR_RESPONSIBLE, contractor_id=other_org.id
    )
    login(client, "web_other2")
    r = client.post(
        f"/prescriptions/{prescription.id}/attachments",
        files={"file": ("x.jpg", b"abc", "image/jpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert db.query(PrescriptionAttachment).count() == 0


# --- печать и подписной скан --------------------------------------------------


def test_print_and_scan_flow(db, client, persons, obj, contractor, tmp_storage):
    p = create_prescription(
        db,
        PrescriptionCreate(
            prescription_type=PT.INTERNAL, doc_number="WEB-INT-1", doc_date=date(2026, 4, 1),
            object_id=obj.id, contractor_id=contractor.id, description="нет ограждения",
            items=[{"text": "Установить ограждение"}],
        ),
        actor_user_id=persons.insp.id,
    )
    login(client, "web_insp")

    assert client.get(f"/prescriptions/{p.id}/print").status_code == 303

    r = client.post(f"/prescriptions/{p.id}/print-version", follow_redirects=False)
    assert r.status_code == 303
    version = db.query(PrescriptionPrintVersion).filter_by(prescription_id=p.id).one()
    assert version.version_number == 1

    pr = client.get(f"/prescriptions/{p.id}/print")
    assert pr.status_code == 200
    assert "pdf" in pr.headers["content-type"]
    audits = db.scalars(
        select(PrescriptionHistory).where(
            PrescriptionHistory.prescription_id == p.id,
            PrescriptionHistory.change_type == "print_requested",
        )
    ).all()
    assert len(audits) == 1

    client.post("/logout")
    login(client, "web_podr")
    assert client.get(f"/prescriptions/{p.id}/print").status_code == 403
    client.post("/logout")

    login(client, "web_insp")
    client.post(
        f"/prescriptions/{p.id}/status",
        data={"new_status": "new", "comment": ""},
        follow_redirects=False,
    )
    db.refresh(p)
    assert p.status == PS.DRAFT_FOR_SIGNATURE

    client.post(
        f"/prescriptions/{p.id}/signed-scan",
        files={"file": ("scan.pdf", b"%PDF signed scan", "application/pdf")},
        follow_redirects=False,
    )
    db.refresh(p)
    assert p.signed_scan_attachment_id is not None
    assert p.signed_scan_verified_at is None

    verify = client.post(f"/prescriptions/{p.id}/verify-scan", follow_redirects=False)
    assert verify.status_code == 303
    db.refresh(p)
    assert p.signed_scan_verified_at is not None

    reg = client.post(
        f"/prescriptions/{p.id}/status",
        data={"new_status": "new", "comment": ""},
        follow_redirects=False,
    )
    assert reg.status_code == 303
    db.refresh(p)
    assert p.status == PS.NEW
    assert p.confirmed_at is not None


# --- админ --------------------------------------------------------------------


def test_admin_pages_rbac(client, persons):
    login(client, "web_podr")
    assert client.get("/admin/users").status_code == 403
    client.post("/logout")
    login(client, "web_adm")
    assert client.get("/admin/users").status_code == 200
    assert client.get("/admin/directories").status_code == 200


def test_admin_create_user_and_role_log(db, client, persons):
    login(client, "web_adm")
    r = client.post(
        "/admin/users",
        data={
            "login": "created1", "full_name": "Созданный Тест", "role": "inspector",
            "email": "", "phone": "", "contractor_id": "", "object_id": "",
            "telegram_chat_id": "5555",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    created = db.scalar(select(User).where(User.login == "created1"))
    assert created is not None and getattr(created, MCP) is True
    ch = db.scalar(
        select(RecipientChannel).where(RecipientChannel.recipient_id == created.id)
    )
    assert ch is not None and ch.address == "5555"

    r = client.post(
        f"/admin/users/{created.id}/role",
        data={"new_role": "manager"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    log = db.query(UserRoleChangeLog).filter_by(user_id=created.id).one()
    assert log.new_role == "manager"


def test_admin_deactivate_blocks_login(db, client, persons, contractor):
    victim = _mk_user(db, "web_victim", UserRole.MANAGER, contractor_id=contractor.id)
    login(client, "web_adm")
    r = client.post(f"/admin/users/{victim.id}/deactivate", follow_redirects=False)
    assert r.status_code == 303
    client.post("/logout")
    assert login(client, "web_victim").status_code == 200
    db.refresh(victim)
    assert victim.is_active is False


def test_new_prescription_via_form(db, client, persons, obj, contractor):
    login(client, "web_insp")
    r = client.post(
        "/prescriptions/new",
        data={
            "prescription_type": "internal",
            "doc_number": "WEB-NEW-9",
            "doc_date": "2026-05-05",
            "object_id": str(obj.id),
            "contractor_id": str(contractor.id),
            "description": "отсутствует маркировка",
            "deadline": "2026-05-20",
            "items_text": "Пункт один\nПункт два",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    p = db.scalar(select(Prescription).where(Prescription.doc_number == "WEB-NEW-9"))
    assert p is not None
    assert p.status == PS.DRAFT_FOR_SIGNATURE
    assert len(p.items) == 2


def test_directories_create(db, client, persons):
    login(client, "web_adm")
    r = client.post(
        "/admin/directories/objects",
        data={"name": "Новый объект Т", "address": "ул. Тестовая"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert db.scalar(select(m.Object).where(m.Object.name == "Новый объект Т")) is not None
    r = client.post(
        "/admin/directories/contractors",
        data={"name": "ООО Тест Подряд", "inn": "7700000000"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert db.scalar(select(m.Contractor).where(m.Contractor.name == "ООО Тест Подряд")) is not None
