"""Справочники админки: добавление записей, дедупликация имён, RBAC,
автосинхронизация каталога инспекторов при создании пользователя-инспектора.
"""

from __future__ import annotations

from sqlalchemy import select

from src.db.models import (
    ConstructionManagement,
    Enterprise,
    PrescriptionCategory,
    PrescriptionInspector,
    PrescriptionKind,
)
from tests.conftest import login

DIR_ENDPOINTS = {
    "enterprises": Enterprise,
    "categories": PrescriptionCategory,
    "kinds": PrescriptionKind,
    "mgmt": ConstructionManagement,
    "issuers": PrescriptionInspector,
}


def _mk_user_payload(login_name: str, full_name: str, role: str) -> dict:
    return {
        "login": login_name,
        "full_name": full_name,
        "role": role,
        "email": "",
        "phone": "",
        "contractor_id": "",
        "object_id": "",
        "telegram_chat_id": "",
    }


def test_directories_page_lists_all_reference_tables(db, client, persons):
    db.add_all([
        Enterprise(name="ТЕСТ Предприятие"),
        PrescriptionCategory(name="ТЕСТ Категория"),
        PrescriptionKind(name="ТЕСТ Вид"),
        ConstructionManagement(name="ТЕСТ Управление"),
        PrescriptionInspector(name="ТЕСТ Выдавший"),
    ])
    db.commit()
    login(client, "web_adm")
    text = client.get("/admin/directories").text
    for name in (
        "ТЕСТ Предприятие",
        "ТЕСТ Категория",
        "ТЕСТ Вид",
        "ТЕСТ Управление",
        "ТЕСТ Выдавший",
    ):
        assert name in text


def test_add_directory_entries_and_dedup(db, client, persons):
    login(client, "web_adm")
    for endpoint, model in DIR_ENDPOINTS.items():
        r = client.post(
            f"/admin/directories/{endpoint}",
            data={"name": f"СД-{endpoint}", "address": "адрес"},
            follow_redirects=False,
        )
        assert r.status_code == 303, endpoint
    db.expire_all()
    for endpoint, model in DIR_ENDPOINTS.items():
        assert db.scalar(select(model).where(model.name == f"СД-{endpoint}")) is not None, endpoint

    for endpoint, model in DIR_ENDPOINTS.items():
        before = db.query(model).count()
        r = client.post(
            f"/admin/directories/{endpoint}",
            data={"name": f"СД-{endpoint}"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        db.expire_all()
        assert db.query(model).count() == before, endpoint

    page = client.get("/admin/directories").text
    assert "уже есть" in page


def test_directory_endpoints_require_admin(client, persons):
    login(client, "web_podr")
    assert client.get("/admin/directories").status_code == 403
    assert client.post(
        "/admin/directories/enterprises", data={"name": "Без доступа"}
    ).status_code == 403
    assert client.post(
        "/admin/directories/categories", data={"name": "Без доступа"}
    ).status_code == 403


def test_create_inspector_user_syncs_issuer_directory(db, client, persons):
    login(client, "web_adm")

    r = client.post(
        "/admin/users",
        data=_mk_user_payload("dir_insp", "Сидоров С.С.", "inspector"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.scalar(
        select(PrescriptionInspector).where(PrescriptionInspector.name == "Сидоров С.С.")
    ) is not None

    client.post(
        "/admin/users",
        data=_mk_user_payload("dir_insp2", "Сидоров С.С.", "inspector"),
        follow_redirects=False,
    )
    db.expire_all()
    assert (
        db.query(PrescriptionInspector).filter_by(name="Сидоров С.С.").count() == 1
    )

    client.post(
        "/admin/users",
        data=_mk_user_payload("dir_mgr", "Кадровик К.К.", "manager"),
        follow_redirects=False,
    )
    db.expire_all()
    assert db.scalar(
        select(PrescriptionInspector).where(PrescriptionInspector.name == "Кадровик К.К.")
    ) is None
