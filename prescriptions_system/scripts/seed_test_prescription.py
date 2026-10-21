"""Создание тестового предписания для проверки RAG (обоснование по нормам).

Скрипт идемпотентен: повторный запуск не создаёт дублей — существующие
объект, подрядчик и предписание переиспользуются.

Примеры:
    python scripts/seed_test_prescription.py
    python scripts/seed_test_prescription.py --pdf
    python scripts/seed_test_prescription.py --kind customer --pdf

--kind internal  — внутреннее предписание (Генподрядчик → Подрядчик),
                   его видно в веб-реестре, инспектор может подобрать нормы
                   кнопкой «Подобрать к п.N» и подтвердить ссылки.
--kind customer — предписание Заказчика: PDF кладётся в inbox/customer,
                   проверяется полный цикл OCR → LLM → RAG.

Данные берутся из data/testdata/rag_test_prescription.json.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz  # noqa: E402
from sqlalchemy import select  # noqa: E402

from src.db.engine import get_session_factory  # noqa: E402
from src.db.models import (  # noqa: E402
    Contractor,
    Object,
    Prescription,
    PrescriptionType,
    User,
    UserRole,
)
from src.observability import configure_logging  # noqa: E402
from src.prescriptions.schemas import PrescriptionCreate  # noqa: E402
from src.prescriptions.service import create_prescription  # noqa: E402
from src.rag.testdata import load_test_prescription  # noqa: E402

logger = logging.getLogger(__name__)


def _ensure_object(db, name: str) -> Object:
    obj = db.scalar(select(Object).where(Object.name == name))
    if obj is None:
        obj = Object(name=name, address="Тестовый адрес, д. 1")
        db.add(obj)
        db.flush()
        logger.info("создан объект: %s", name)
    return obj


def _ensure_contractor(db, name: str) -> Contractor:
    contractor = db.scalar(select(Contractor).where(Contractor.name == name))
    if contractor is None:
        contractor = Contractor(name=name, inn=None)
        db.add(contractor)
        db.flush()
        logger.info("создан подрядчик: %s", name)
    return contractor


def _find_inspector(db) -> User | None:
    return db.scalar(
        select(User)
        .where(User.role.in_([UserRole.INSPECTOR, UserRole.ADMIN]), User.is_active.is_(True))
        .order_by(User.id)
    )


def _render_customer_pdf(
    doc_number: str,
    doc_date: str,
    deadline: str,
    object_name: str,
    contractor_name: str,
    items: list[str],
) -> bytes:
    """PDF предписания Заказчика с текстовым слоем (быстрый путь PyMuPDF).

    Формулировки подобраны так, чтобы regex-парсеры Шага 5 вытащили номер,
    дату, объект, подрядчика, срок и нумерованные пункты. Страница широкая,
    шрифт мелкий: каждый пункт должен помещаться в одну строку, иначе
    RE_ITEM захватит только первую строку пункта.
    """
    items_html = "".join(
        f"<li>{html_lib.escape(text)}</li>" for text in items
    )
    body = f"""
        <h2>ПРЕДПИСАНИЕ № {html_lib.escape(doc_number)} от {html_lib.escape(doc_date)}</h2>
        <p><b>Объект:</b> {html_lib.escape(object_name)}</p>
        <p><b>Подрядчик:</b> {html_lib.escape(contractor_name)}</p>
        <p><b>Нарушение:</b> замечания по результатам проверки приведены ниже.</p>
        <p><b>Срок устранения:</b> {html_lib.escape(deadline)}</p>
        <ol>{items_html}</ol>
        <p><b>Ответственный:</b> начальник участка</p>
    """
    doc = fitz.open()
    page = doc.new_page(width=1191, height=842)  # A3 landscape: пункт в одну строку
    page.insert_htmlbox(
        fitz.Rect(30, 30, 1161, 812),
        f'<div style="font-size: 7pt">{body}</div>',
    )
    data = doc.tobytes(deflate=True)
    doc.close()
    return data


def _write_customer_pdf(meta, items: list[str]) -> Path:
    from src.config import get_settings

    settings = get_settings()
    inbox = settings.resolve_path(settings.storage.inbox_customer)
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / f"{meta.doc_number.replace('/', '_')}.pdf"
    target.write_bytes(
        _render_customer_pdf(
            doc_number=meta.doc_number,
            doc_date=meta.doc_date.strftime("%d.%m.%Y"),
            deadline=meta.deadline.strftime("%d.%m.%Y") if meta.deadline else "не задан",
            object_name=meta.object_name,
            contractor_name=meta.contractor_name,
            items=items,
        )
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Создать тестовое предписание для проверки RAG"
    )
    parser.add_argument(
        "--kind",
        choices=["internal", "customer", "both"],
        default="internal",
        help="какое предписание создавать (по умолчанию internal)",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="дополнительно сформировать PDF предписания Заказчика в inbox/customer",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="вывести результат в JSON (для автоматизации)",
    )
    args = parser.parse_args()

    configure_logging("seed_test_prescription")

    fixture = load_test_prescription()
    kinds = ["internal", "customer"] if args.kind == "both" else [args.kind]

    db = get_session_factory()()
    result: dict = {"created": [], "reused": [], "pdf": []}
    try:
        for kind in kinds:
            meta = fixture.meta_for(kind)
            obj = _ensure_object(db, meta.object_name)
            contractor = _ensure_contractor(db, meta.contractor_name)
            db.commit()

            existing = db.scalar(
                select(Prescription).where(
                    Prescription.doc_number == meta.doc_number,
                    Prescription.doc_date == meta.doc_date,
                    Prescription.object_id == obj.id,
                    Prescription.prescription_type
                    == (
                        PrescriptionType.INTERNAL
                        if kind == "internal"
                        else PrescriptionType.CUSTOMER
                    ),
                )
            )
            if existing is not None:
                result["reused"].append(
                    {"kind": kind, "id": existing.id, "doc_number": existing.doc_number}
                )
                prescription = existing
            else:
                data = PrescriptionCreate(
                    prescription_type=(
                        PrescriptionType.INTERNAL
                        if kind == "internal"
                        else PrescriptionType.CUSTOMER
                    ),
                    doc_number=meta.doc_number,
                    doc_date=meta.doc_date,
                    object_id=obj.id,
                    contractor_id=contractor.id,
                    description=meta.description,
                    deadline=meta.deadline,
                    items=[
                        {"text": item.text, "deadline": meta.deadline}
                        for item in fixture.items
                    ],
                )
                actor = _find_inspector(db)
                prescription = create_prescription(
                    db,
                    data,
                    actor_user_id=actor.id if actor is not None else None,
                    as_system=actor is None,
                )
                result["created"].append(
                    {
                        "kind": kind,
                        "id": prescription.id,
                        "doc_number": prescription.doc_number,
                        "items": len(prescription.items),
                    }
                )

            if args.pdf and kind == "customer":
                pdf_path = _write_customer_pdf(
                    meta, [item.text for item in fixture.items]
                )
                result["pdf"].append(str(pdf_path))

        if args.pdf and "customer" not in kinds:
            # PDF всегда делается по клиентской части фикстуры: он попадает
            # в inbox и обрабатывается OCR-пайплайном как предписание Заказчика.
            customer_meta = fixture.meta_for("customer")
            result["pdf"].append(
                str(
                    _write_customer_pdf(
                        customer_meta, [item.text for item in fixture.items]
                    )
                )
            )
    finally:
        db.close()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for entry in result["created"]:
            print(
                f"Создано предписание [{entry['kind']}] id={entry['id']} "
                f"№ {entry['doc_number']} (пунктов: {entry.get('items', 0)})"
            )
        for entry in result["reused"]:
            print(
                f"Уже существует [{entry['kind']}] id={entry['id']} "
                f"№ {entry['doc_number']} — дубль не создан"
            )
        for path in result["pdf"]:
            print(f"PDF для OCR-пайплайна: {path}")
        if not result["created"] and not result["reused"]:
            print("Ничего не создано.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
