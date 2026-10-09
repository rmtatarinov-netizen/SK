"""Импорт предписаний в реестр из CSV/XLSX: разбор файла, сервис импорта, веб-загрузка.

Стиль импорта повторяет users.service.import_users_from_csv: идемпотентность
по ключу предписания, авто-создание справочников, построчные ошибки.
"""

from __future__ import annotations

import io
from datetime import date, datetime

import pytest
from sqlalchemy import select

from src.db import models as m
from src.db.models import Prescription, PrescriptionHistory
from src.prescriptions import importer
from tests.conftest import login

HEADER = ",".join(importer.IMPORT_COLUMNS)


def _row(**kw) -> dict:
    row = {column: "" for column in importer.IMPORT_COLUMNS}
    row.update(BASE_ROW)
    row.update(kw)
    return row


def _csv_text(rows: list[dict], delimiter: str = ",") -> str:
    lines = [delimiter.join(importer.IMPORT_COLUMNS)]
    for row in rows:
        lines.append(delimiter.join(str(row[c]) for c in importer.IMPORT_COLUMNS))
    return "\n".join(lines) + "\n"


BASE_ROW = dict(
    doc_number="IMP-1",
    doc_date="2026-10-01",
    type="internal",
    object="Объект Импорт",
    contractor="ООО Импорт Подряд",
    description="Отсутствует ограждение",
)


# --- read_table ---------------------------------------------------------------------


def test_read_table_csv_comma_and_semicolon():
    text_comma = _csv_text([_row(**BASE_ROW)])
    rows = importer.read_table(text_comma.encode("utf-8-sig"), "imp.csv")
    assert len(rows) == 1 and rows[0]["doc_number"] == "IMP-1"

    text_semi = _csv_text([_row(**BASE_ROW)], delimiter=";")
    rows = importer.read_table(text_semi.encode("utf-8"), "imp.csv")
    assert rows[0]["contractor"] == "ООО Импорт Подряд"


def test_read_table_rejects_unknown_format_and_missing_columns():
    with pytest.raises(ValueError, match="csv"):
        importer.read_table(b"doc_number\n", "file.txt")
    with pytest.raises(ValueError, match="doc_number"):
        importer.read_table("a;b\n1;2\n".encode("utf-8"), "bad.csv")


def test_read_table_xlsx():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.append(importer.IMPORT_COLUMNS)
    sheet.append([
        "IMP-X", datetime(2026, 10, 5), "customer", "Объект Икс", "ООО Икс", "", "", "", "", "",
        "описание из excel", "", None, None, None, "", None, "Фамилов Ф.Ф.",
    ])
    buf = io.BytesIO()
    wb.save(buf)
    rows = importer.read_table(buf.getvalue(), "imp.xlsx")
    assert len(rows) == 1
    assert rows[0]["doc_number"] == "IMP-X"
    assert rows[0]["doc_date"] == datetime(2026, 10, 5)


# --- сервис импорта -------------------------------------------------------------------


def test_import_creates_with_status_from_file(db, obj, contractor, admin):
    payload = (
        "doc_number,doc_date,type,object,contractor,enterprise,category,kind,"
        "description,justification,deadline,status,closed_at,responsible\n"
        "IMP-FULL,2026-10-01,customer,Объект Новый,ООО Новый Подряд,Завод А,К1,В1,"
        "полная строка импорта,обоснование,2026-10-20,closed,2026-09-30 12:30:00," +
        contractor_user_full_name(db, contractor) + "\n"
    )
    rows = importer.read_table(payload.encode("utf-8-sig"), "full.csv")
    result = importer.import_prescriptions(db, rows, actor_user_id=admin.id)

    assert result["created"] == ["IMP-FULL"]
    assert result["errors"] == []
    assert len(result["new_refs"]) >= 4  # объект, подрядчик, предприятие, категория, вид

    p = db.scalar(select(Prescription).where(Prescription.doc_number == "IMP-FULL"))
    assert p is not None
    assert p.status == m.PrescriptionStatus.CLOSED
    assert p.closed_at.strftime("%Y-%m-%d %H:%M") == "2026-09-30 12:30"
    assert p.confirmed_at is not None
    assert p.requires_review is False
    assert p.object.name == "Объект Новый"
    assert p.enterprise.name == "Завод А"
    assert p.category_id is not None and p.kind_id is not None
    assert db.query(m.PrescriptionCategory).filter_by(name="К1").count() == 1
    assert db.query(m.PrescriptionKind).filter_by(name="В1").count() == 1
    assert p.responsible_user_id is not None

    hist = db.scalar(
        select(PrescriptionHistory).where(
            PrescriptionHistory.prescription_id == p.id,
            PrescriptionHistory.change_type == "imported_status",
        )
    )
    assert hist is not None


def contractor_user_full_name(db, contractor) -> str:
    from src.db.models import User

    user = db.scalar(
        select(User).where(User.contractor_id == contractor.id, User.role == m.UserRole.CONTRACTOR_RESPONSIBLE)
    )
    if user is None:
        user = User(
            login="imp_resp", full_name="Импорт Ответств.",
            role=m.UserRole.CONTRACTOR_RESPONSIBLE, password_hash="x",
            contractor_id=contractor.id,
        )
        db.add(user)
        db.commit()
    return user.full_name


def test_import_default_status_by_type(db, obj, contractor, admin):
    rows = [_row(doc_number="IMP-CUST", type="customer")]
    result = importer.import_prescriptions(db, rows, actor_user_id=admin.id)
    assert result["created"] == ["IMP-CUST"]
    p = db.scalar(select(Prescription).where(Prescription.doc_number == "IMP-CUST"))
    assert p.status == m.PrescriptionStatus.NEW
    assert p.requires_review is True

    rows = [_row(doc_number="IMP-INT", type="внутреннее")]
    importer.import_prescriptions(db, rows, actor_user_id=admin.id)
    p2 = db.scalar(select(Prescription).where(Prescription.doc_number == "IMP-INT"))
    assert p2.status == m.PrescriptionStatus.DRAFT_FOR_SIGNATURE


def test_import_is_idempotent(db, obj, contractor, admin):
    rows = [_row(**BASE_ROW)]
    first = importer.import_prescriptions(db, rows, actor_user_id=admin.id)
    second = importer.import_prescriptions(db, [_row(**BASE_ROW)], actor_user_id=admin.id)
    assert first["created"] == ["IMP-1"]
    assert second["skipped"] == ["IMP-1"] and second["created"] == []
    assert db.query(Prescription).filter_by(doc_number="IMP-1").count() == 1


def test_import_collects_row_errors(db, obj, contractor, admin):
    rows = [
        _row(doc_number="IMP-BAD-DATE", doc_date="abc"),
        _row(doc_number="IMP-BAD-TYPE", type="weekly"),
        _row(doc_number="IMP-BAD-STATUS", status="done"),
        _row(doc_number="IMP-OK-2"),
    ]
    result = importer.import_prescriptions(db, rows, actor_user_id=admin.id)
    assert result["created"] == ["IMP-OK-2"]
    assert len(result["errors"]) == 3
    assert "строка 2" in result["errors"][0]
    assert "done" in result["errors"][2]
    assert db.query(Prescription).filter_by(doc_number="IMP-1").count() == 0
    assert db.query(Prescription).filter_by(doc_number="IMP-BAD-STATUS").count() == 0
    # авто-справочники из удачной строки не плодят дублей и не потеряны
    assert db.query(m.Object).filter_by(name="Объект Импорт").count() == 1


def test_import_unknown_responsible_kept_without_assignment(db, obj, contractor, admin):
    rows = [_row(responsible="Неизвестный Не.Не.")]
    result = importer.import_prescriptions(db, rows, actor_user_id=admin.id)
    assert result["created"] == ["IMP-1"]
    assert result["no_responsible"] == ["IMP-1: Неизвестный Не.Не."]
    p = db.scalar(select(Prescription).where(Prescription.doc_number == "IMP-1"))
    assert p.responsible_user_id is None


def test_import_requires_inspector_or_admin(db, obj, contractor, contractor_user):
    with pytest.raises(PermissionError):
        importer.import_prescriptions(db, [_row(**BASE_ROW)], actor_user_id=contractor_user.id)
    with pytest.raises(PermissionError):
        importer.import_prescriptions(db, [_row(**BASE_ROW)], actor_user_id=None)


# --- веб --------------------------------------------------------------------------------


def test_import_upload_via_web(db, client, persons, obj, contractor):
    payload = _csv_text([_row(doc_number="WEB-IMP-1")]).encode("utf-8-sig")
    login(client, "web_insp")
    r = client.post(
        "/prescriptions/import",
        files={"file": ("imp.csv", payload, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    p = db.scalar(select(Prescription).where(Prescription.doc_number == "WEB-IMP-1"))
    assert p is not None
    page = client.get("/prescriptions").text
    assert "WEB-IMP-1" in page
    assert "Импорт: создано 1" in page


def test_import_upload_contractor_forbidden(db, client, persons, obj, contractor):
    payload = _csv_text([_row(**BASE_ROW)]).encode("utf-8-sig")
    login(client, "web_podr")
    r = client.post(
        "/prescriptions/import",
        files={"file": ("imp.csv", payload, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert db.query(Prescription).filter_by(doc_number="IMP-1").count() == 0


def test_import_upload_bad_file_flashes_error(client, persons):
    login(client, "web_adm")
    r = client.post(
        "/prescriptions/import",
        files={"file": ("notes.txt", b"whatever", "text/plain")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Ошибка импорта" in client.get("/prescriptions").text


def test_import_template_download(db, client, persons, obj, contractor):
    login(client, "web_insp")
    r = client.get("/prescriptions/import/template.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    header = body.strip().splitlines()[0]
    assert header.startswith("№,Дата выдачи,Дата устранения,Тип,Объект,Подрядчик")
    assert len(body.strip().splitlines()) == 1  # только заголовки
    assert db.query(Prescription).count() == 0


# --- формат окна «Реестр предписаний» (русские заголовки) ----------------------------

RU_HEADER = (
    "№;Дата выдачи;Дата устранения;Тип;Объект;Подрядчик;Категория ;Вид;"
    "Описание нарушений;ФИО отв. за устр.;Инспектор;Статус;Срок"
)


def test_import_russian_registry_layout_with_russian_statuses(db, obj, contractor, admin, inspector):
    line1 = ";".join([
        "АРХ-1", "05.10.2021", "11.10.2021", "customer", "311-КЖ", "", "",
        "Нарушение последовательности работ", "Армирование ФМ-3", "",
        "Желнин Е.А.", "Закрыто", "до 30.10.2021",
    ])
    line2 = ";".join([
        "АРХ-2", "06.10.2021", "", "customer", "307-КМ1", "", "",
        "Сварочные работы", "Швы", "", inspector.full_name, "В работе", "20.10.2021",
    ])
    text = f"{RU_HEADER}\n{line1}\n{line2}\n\n"  # пустая хвостовая строка
    rows = importer.read_table(text.encode("utf-8-sig"), "архив.csv")
    assert len(rows) == 2

    result = importer.import_prescriptions(db, rows, actor_user_id=admin.id)
    assert result["created"] == ["АРХ-1", "АРХ-2"], result["errors"]
    assert result["errors"] == []

    p1 = db.scalar(select(Prescription).where(Prescription.doc_number == "АРХ-1"))
    assert p1.status == m.PrescriptionStatus.CLOSED
    assert p1.contractor.name == "Не указан"
    assert p1.actual_fix_date == date(2021, 10, 11)
    assert p1.deadline == date(2021, 10, 30)  # извлекается из «до 30.10.2021»
    assert p1.description == "Армирование ФМ-3"
    assert p1.kind_id is not None
    assert result["no_inspector"] == ["АРХ-1: Желнин Е.А."]

    p2 = db.scalar(select(Prescription).where(Prescription.doc_number == "АРХ-2"))
    assert p2.status == m.PrescriptionStatus.IN_WORK
    assert p2.inspector_user_id == inspector.id


def test_import_invalid_text_deadline_is_warning_not_error(db, obj, contractor, admin):
    line = ";".join([
        "АРХ-БОГ", "27.10.2022", "", "customer", "329-ТХ", "Нет данных", "",
        "Вид работ", "монтаж с отклонением", "", "", "Закрыто", "31.11.2022",
    ])
    text = f"{RU_HEADER}\n{line}\n"
    rows = importer.read_table(text.encode("utf-8-sig"), "bogus.csv")
    result = importer.import_prescriptions(db, rows, actor_user_id=admin.id)
    assert result["created"] == ["АРХ-БОГ"]
    assert result["errors"] == []
    assert any("Срок" in w for w in result["warnings"])
    p = db.scalar(select(Prescription).where(Prescription.doc_number == "АРХ-БОГ"))
    assert p.deadline is None
    assert p.status == m.PrescriptionStatus.CLOSED
    assert p.contractor.name == "Нет данных"


def test_import_export_format_roundtrip(db, obj, contractor, admin):
    from src.prescriptions.schemas import PrescriptionCreate
    from src.prescriptions.service import create_prescription
    from src.reports import generator

    for number in ("EXP-1", "EXP-2"):
        create_prescription(
            db,
            PrescriptionCreate(
                prescription_type=m.PrescriptionType.CUSTOMER,
                doc_number=number,
                doc_date=date(2026, 3, 1),
                object_id=obj.id,
                contractor_id=contractor.id,
                description="нарушение из экспорта",
            ),
            actor_user_id=admin.id,
        )

    text = generator.export_csv(db)
    rows = importer.read_table(text.encode("utf-8-sig"), "export.csv")
    result = importer.import_prescriptions(db, rows, actor_user_id=admin.id)
    assert set(result["skipped"]) == {"EXP-1", "EXP-2"}
    assert result["created"] == [] and result["errors"] == []
