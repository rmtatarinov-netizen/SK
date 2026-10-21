"""Тесты RAG по нормативной базе (Шаг 7).

Ollama и сеть не используются: эмбеддинги — детерминированный фейк,
векторное хранилище — Chroma ephemeral, LLM — FakeLLMClient.
"""

from __future__ import annotations

import hashlib
import os
import re

import pytest
from pathlib import Path

from sqlalchemy import func, select

from src.db.models import (
    NormChunk,
    NormChunkEmbedding,
    NormDoc,
    NormRefSource,
    NormRefStatus,
    Prescription,
    PrescriptionNormRef,
)
from src.rag import embeddings
from src.rag.chunking import chunk_text
from src.rag.ingest_norms import ingest_document
from src.rag.recommend import recommend_for_text, save_proposed_refs
from src.rag.search import search_topk
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
    from uuid import uuid4

    client = chromadb.EphemeralClient()
    # имя уникально: EphemeralClient chiaет состояние в рамках процесса
    return client.get_or_create_collection(
        name=f"norm_docs_{uuid4().hex[:8]}", metadata={"hnsw:space": "cosine"}
    )


@pytest.fixture()
def norm_text(tmp_path):
    text = (
        "5.1 Защитный слой бетона должен соответствовать проекту.\n"
        "5.2 Бетонные конструкции требуют ухода.\n"
        "5.3 Толщина защитного слоя арматуры нормируется.\n"
    )
    file = tmp_path / "sp_63.txt"
    file.write_text(text, encoding="utf-8")
    return file


# --- чанкинг ----------------------------------------------------------------


def test_chunk_detects_items_and_boundaries():
    text = (
        "5.1 Первый пункт норм про бетон достаточной длины для чанка.\n"
        "5.2 Второй пункт норм про уход за бетоном тоже достаточно длинный.\n"
    )
    chunks = chunk_text(text, chunk_size_tokens=8, overlap_tokens=2)
    assert len(chunks) >= 2
    assert chunks[0].item == "5.1"
    assert chunks[1].item == "5.2"


def test_chunk_overlap_present():
    para = "Предложение одно. " * 40
    chunks = chunk_text(para, chunk_size_tokens=20, overlap_tokens=10)
    assert len(chunks) >= 2
    tail_words = set(re.findall(r"\w+", chunks[0].text)) & set(re.findall(r"\w+", chunks[1].text))
    assert tail_words  # перекрытие перенесено во второй чанк


def test_chunk_empty():
    assert chunk_text("   ") == []


# --- импорт -----------------------------------------------------------------


def test_ingest_persists_doc_chunks_and_vectors(db, collection, norm_text, fake_embed):
    doc = ingest_document(db, collection, norm_text, code="СП 63.13330.2018", title="Бетон")
    assert doc.id is not None
    chunks = db.scalars(select(NormChunk).where(NormChunk.document_id == doc.id)).all()
    assert len(chunks) >= 1
    assert collection.count() == len(chunks)
    emb = db.scalars(select(NormChunkEmbedding)).all()
    assert len(emb) == len(chunks)
    assert all(e.model for e in emb)


def test_ingest_replace_is_idempotent(db, collection, norm_text, fake_embed):
    ingest_document(db, collection, norm_text, code="СП 63.13330.2018")
    first = db.scalar(select(func.count()).select_from(NormChunk))
    ingest_document(db, collection, norm_text, code="СП 63.13330.2018", edition="ред2")
    second = db.scalar(select(func.count()).select_from(NormChunk))
    docs = db.scalar(select(func.count()).select_from(NormDoc))
    assert docs == 1
    assert second == first  # не накапливает дубли при перезагрузке


# --- поиск ------------------------------------------------------------------


def test_search_topk_returns_matching_fragment(db, collection, norm_text, fake_embed):
    ingest_document(db, collection, norm_text, code="СП 63.13330.2018")
    hits = search_topk(collection, "защитный слой бетона", top_k=2, embedder=fake_embed)
    assert hits
    assert hits[0]["code"] == "СП 63.13330.2018"
    assert "слой" in hits[0]["fragment"]


def test_search_empty_collection_returns_nothing(collection, fake_embed):
    assert search_topk(collection, "что угодно", embedder=fake_embed) == []


# --- рекомендации (RAG + LLM) ----------------------------------------------


def test_recommend_uses_metadata_not_model_code(db, collection, norm_text, fake_embed, prescription):
    doc = ingest_document(db, collection, norm_text, code="СП 63.13330.2018")
    client = FakeLLMClient([{"matches": [{"index": 0, "reason": "про слой"}]}])
    recs = recommend_for_text(client, collection, "обеспечить защитный слой", db=db)
    assert len(recs) == 1
    assert recs[0].doc_code == "СП 63.13330.2018"  # код из метаданных фрагмента
    assert recs[0].reason == "про слой"


def test_recommend_drops_bogus_index(db, collection, norm_text, fake_embed, prescription):
    ingest_document(db, collection, norm_text, code="СП 63.13330.2018")
    client = FakeLLMClient([{"matches": [{"index": 99, "reason": "выдумка"}]}])
    assert recommend_for_text(client, collection, "слой", db=db) == []


def test_recommend_drops_code_absent_from_base(db, collection, norm_text, fake_embed, prescription):
    """Ссылка, которой нет в загруженной базе, не выдаётся (запрет ТЗ)."""
    ingest_document(db, collection, norm_text, code="СП 63.13330.2018")
    client = FakeLLMClient([{"matches": [{"index": 0, "reason": "r"}]}])
    # удалим документ из справочника — ссылка больше не подтверждается базой
    doc = db.scalar(select(NormDoc))
    db.execute(
        __import__("sqlalchemy").delete(NormChunkEmbedding).where(
            NormChunkEmbedding.chunk_id.in_(
                select(NormChunk.id).where(NormChunk.document_id == doc.id)
            )
        )
    )
    db.execute(__import__("sqlalchemy").delete(NormChunk).where(NormChunk.document_id == doc.id))
    db.delete(doc)
    db.commit()
    assert recommend_for_text(client, collection, "слой", db=db) == []


def test_save_proposed_refs_suggested_and_idempotent(db, collection, norm_text, fake_embed, prescription):
    ingest_document(db, collection, norm_text, code="СП 63.13330.2018")
    client = FakeLLMClient([{"matches": [{"index": 0, "reason": "r"}]}])
    recs = recommend_for_text(client, collection, "слой", db=db)
    created = save_proposed_refs(db, prescription, recs)
    assert created == 1
    saved = db.scalars(
        select(PrescriptionNormRef).where(
            PrescriptionNormRef.prescription_id == prescription.id
        )
    ).all()
    assert saved[0].status == NormRefStatus.SUGGESTED
    assert saved[0].source == NormRefSource.RAG
    # повторное сохранение не создаёт дубль
    assert save_proposed_refs(db, prescription, recs) == 0


def test_ascii_chroma_path_passes_ascii_untouched(tmp_path):
    from src.rag.search import ascii_chroma_path

    p = Path("C:/ascii/directory/chroma")
    assert ascii_chroma_path(p) == str(p)


@pytest.mark.skipif(os.name != "nt", reason="8.3-имена каталогов есть только на Windows")
def test_ascii_chroma_path_shortens_nonascii(tmp_path):
    from src.rag.search import ascii_chroma_path

    p = tmp_path / "Проверка Хрома"
    p.mkdir()
    result = ascii_chroma_path(p)
    if not result.isascii():
        pytest.skip("на этом томе отключены 8.3-имена")
    assert Path(result).exists()
    assert Path(result).samefile(p)
