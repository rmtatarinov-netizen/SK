"""Оффлайн-проверки обоснования RAG и колонки «Обоснование».

Сеть и Ollama не используются: эмбеддинги — детерминированный фейк,
Chroma — ephemeral, LLM — FakeLLMClient. Живой прогон —
scripts/check_rag_justification.py.
"""

from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select

from src.config import PROJECT_ROOT
from src.db.models import (
    LlmRagLog,
    NormRefStatus,
    PrescriptionHistory,
    PrescriptionItem,
    PrescriptionNormRef,
)
from src.rag import embeddings
from src.rag.ingest_norms import ingest_document
from src.rag.recommend import (
    JUSTIFICATION_HEADER,
    ProposedNormRef,
    build_justification,
    generate_justification,
)
from src.rag.testdata import load_test_prescription
from src.prescriptions.importer import template_csv
from src.prescriptions.service import apply_generated_justification
from tests.conftest import login
from tests.fakes.llm import FakeLLMClient

DIM = 64


def _fake_embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for token in re.findall(r"\w+", (text or "").lower()):
        idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % DIM
        vec[idx] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


@pytest.fixture()
def fake_embed(monkeypatch):
    monkeypatch.setattr(embeddings, "embed_text", _fake_embed)
    return _fake_embed


@pytest.fixture()
def collection():
    import chromadb

    client = chromadb.EphemeralClient()
    return client.get_or_create_collection(
        name=f"norm_docs_{uuid4().hex[:8]}", metadata={"hnsw:space": "cosine"}
    )


@pytest.fixture()
def norm_file(tmp_path):
    file = tmp_path / "sp_63.txt"
    file.write_text(
        "5.1 Защитный слой бетона должен соответствовать проекту.\n"
        "5.2 Толщина защитного слоя арматуры нормируется.\n",
        encoding="utf-8",
    )
    return file


# --- фикстура тестового предписания -----------------------------------------


def test_fixture_has_six_working_and_one_control_item():
    fixture = load_test_prescription()
    assert len(fixture.items) == 7
    assert len(fixture.working_items()) == 6
    assert len(fixture.control_items()) == 1
    assert all(item.expect for item in fixture.working_items())
    assert fixture.control_items()[0].expect == []
    assert fixture.meta_for("internal").doc_number == "ТЕСТ-РП-001/2026"
    assert fixture.meta_for("customer").doc_number == "ТЕСТ-ЗАК-001/2026"
    # ожидаемые коды существуют в загруженной базе (проверка живым стендом)
    for item in fixture.working_items():
        assert item.text.strip()


# --- сборка обоснования ------------------------------------------------------


def test_build_justification_has_code_item_reason_and_fragment():
    refs = [
        ProposedNormRef(
            doc_code="СП 63.13330.2018",
            item="5.3",
            fragment="Толщина защитного слоя арматуры нормируется проектом.",
            chunk_id=None,
            reason="нарушен защитный слой",
            distance=0.2,
        )
    ]
    text = build_justification(refs)
    assert text.startswith(JUSTIFICATION_HEADER)
    assert "СП 63.13330.2018" in text
    assert "п. 5.3" in text
    assert "нарушен защитный слой" in text
    assert "Толщина защитного слоя" in text


def test_build_justification_empty_for_no_refs():
    assert build_justification([]) == ""


def test_build_justification_truncates_fragment():
    refs = [
        ProposedNormRef("СП 1", None, "слово " * 200, None, None, None)
    ]
    text = build_justification(refs, max_fragment_chars=40)
    assert "…" in text
    assert len(text.split("Фрагмент: «", 1)[1]) < 200


# --- генерация через RAG + LLM ------------------------------------------------


def test_generate_justification_offline(db, collection, norm_file, fake_embed, prescription):
    ingest_document(db, collection, norm_file, code="СП 63.13330.2018", title="Бетон")
    client = FakeLLMClient([{"matches": [{"index": 0, "reason": "про слой"}]}])
    refs, text = generate_justification(
        client,
        collection,
        "обеспечить защитный слой",
        db=db,
        prescription_id=prescription.id,
    )
    assert len(refs) == 1
    assert refs[0].doc_code == "СП 63.13330.2018"
    assert "СП 63.13330.2018" in text
    log = db.scalars(select(LlmRagLog)).one()
    assert log.prescription_id == prescription.id


# --- сохранение обоснования ---------------------------------------------------


def test_apply_generated_justification_persists_item_and_summary(db, prescription, admin):
    item = PrescriptionItem(
        prescription_id=prescription.id, item_number=1, text="Нарушен слой"
    )
    db.add(item)
    db.commit()

    saved = apply_generated_justification(
        db,
        prescription,
        "Сводное обоснование",
        actor_user_id=admin.id,
        item_justifications={item.id: "Обоснование пункта"},
    )
    assert saved is True
    db.refresh(item)
    db.refresh(prescription)
    assert item.justification == "Обоснование пункта"
    assert prescription.justification == "Сводное обоснование"

    history = db.scalars(
        select(PrescriptionHistory).where(
            PrescriptionHistory.prescription_id == prescription.id,
            PrescriptionHistory.change_type == "data_updated",
        )
    ).all()
    assert history

    # повтор с тем же текстом ничего не меняет
    assert (
        apply_generated_justification(
            db,
            prescription,
            "Сводное обоснование",
            actor_user_id=admin.id,
            item_justifications={item.id: "Обоснование пункта"},
        )
        is False
    )


def test_apply_generated_justification_keeps_summary_when_only_items(db, prescription, admin):
    item = PrescriptionItem(
        prescription_id=prescription.id, item_number=1, text="Нарушен слой"
    )
    db.add(item)
    db.commit()
    prescription.justification = "Старое сводное"
    db.commit()

    apply_generated_justification(
        db,
        prescription,
        "",
        actor_user_id=admin.id,
        item_justifications={item.id: "Только пункт"},
    )
    db.refresh(prescription)
    assert prescription.justification == "Старое сводное"


# --- порядок колонки «Обоснование» -------------------------------------------


def test_column_justification_follows_description(engine):
    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns("prescriptions")]
    assert columns[columns.index("description") + 1] == "justification"

    item_columns = [c["name"] for c in inspector.get_columns("prescription_items")]
    assert item_columns[item_columns.index("text") + 1] == "justification"


def test_registry_template_has_justification_right_after_description():
    html = (PROJECT_ROOT / "src/web/templates/prescriptions/list.html").read_text(
        encoding="utf-8"
    )
    assert "<th>Описание нарушений</th>\n      <th>Обоснование</th>" in html


def test_import_template_has_justification_right_after_description():
    header = template_csv().splitlines()[0].split(",")
    assert header[header.index("Описание нарушений") + 1] == "Обоснование"


# --- маршрут «Подобрать и подставить обоснование» ----------------------------


def test_justify_route_saves_item_and_summary(
    db, client, persons, prescription, monkeypatch
):
    from src.web.routes import prescriptions as pr_routes
    from src.rag import recommend as rr

    item = PrescriptionItem(
        prescription_id=prescription.id, item_number=1, text="Нарушен слой"
    )
    db.add(item)
    db.commit()
    fake_ref = ProposedNormRef(
        doc_code="СП 63.13330.2018", item="5.1", fragment="слой",
        chunk_id=None, reason="r", distance=None,
    )
    justification = "1. СП 63.13330.2018, п. 5.1 — r"

    monkeypatch.setattr(
        pr_routes,
        "rag_recommend",
        SimpleNamespace(
            generate_justification=lambda *a, **k: ([fake_ref], justification),
            save_proposed_refs=rr.save_proposed_refs,
            build_justification=rr.build_justification,
        ),
    )
    monkeypatch.setattr(pr_routes, "OllamaClient", lambda: object())
    monkeypatch.setattr(pr_routes, "get_collection", lambda: None)

    login(client, "web_insp")
    r = client.post(
        f"/prescriptions/{prescription.id}/justification", follow_redirects=False
    )
    assert r.status_code == 303
    db.refresh(item)
    db.refresh(prescription)
    assert item.justification == justification
    assert "СП 63.13330.2018" in (prescription.justification or "")
    refs = db.scalars(
        select(PrescriptionNormRef).where(
            PrescriptionNormRef.prescription_id == prescription.id,
            PrescriptionNormRef.status == NormRefStatus.SUGGESTED,
        )
    ).all()
    assert [ref.doc_code for ref in refs] == ["СП 63.13330.2018"]
