"""Хранилище и поиск по чанкам нормативных документов (Шаг 7).

ChromaDB persistent; векторы считаются локальной bge-m3. RAG ничего не
подтверждает: пустая база или отсутствие релевантных фрагментов — пустой
результат. Ссылка, отсутствующая в загруженной базе, не выдаётся.
"""

from __future__ import annotations

import ctypes
import logging
import os
from collections.abc import Callable
from pathlib import Path

from src.config import get_settings
from src.rag import embeddings

logger = logging.getLogger(__name__)


def ascii_chroma_path(path: Path) -> str:
    """Путь для ChromaDB, пригодный для Windows-сборки hnswlib.

    chromadb 1.0.x на Windows не умеет работать с каталогами, чьи имена
    содержат не-ASCII символы (профиль пользователя вида ``C:\\Users\\Иван``):
    SQLite-часть открывается, а HNSW-сегменты записать/прочитать нельзя —
    база выглядит живой, но при переоткрытии падает с
    "Error loading hnsw index". Лечится 8.3-кратким именем того же каталога
    (``C:\\Users\\73B5~1\\...``): оно ASCII и указывает на те же файлы.
    """
    raw = str(path)
    if os.name != "nt" or raw.isascii():
        return raw
    try:
        get_short = ctypes.windll.kernel32.GetShortPathNameW
        length = get_short(raw, None, 0)
        if length:
            buffer = ctypes.create_unicode_buffer(length)
            written = get_short(raw, buffer, length)
            short = buffer.value if 0 < written < length else ""
            if short.isascii():
                logger.debug("ChromaDB: не-ASCII путь %s заменён на 8.3 %s", raw, short)
                return short
            logger.warning(
                "ChromaDB: краткое имя %s тоже содержит не-ASCII символы; "
                "индекс HNSW может не сохраниться. Перенесите каталог %s "
                "на путь только с латинскими буквами.",
                short, raw,
            )
    except Exception:  # pragma: no cover - зависит от системного API
        logger.exception("ChromaDB: не удалось получить 8.3-имя для %s", raw)
    return raw


def get_collection():
    """Открыть persistent-коллекцию нормативных чанков (cosine)."""
    import chromadb

    settings = get_settings()
    path = settings.resolve_path(settings.rag.persist_directory)
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=ascii_chroma_path(path))
    return client.get_or_create_collection(
        name=settings.rag.collection,
        metadata={"hnsw:space": "cosine"},
    )


def search_topk(
    collection,
    query: str,
    top_k: int | None = None,
    *,
    embedder: Callable[[str], list[float]] | None = None,
) -> list[dict]:
    """Топ-k релевантных фрагментов: document_id, chunk_id, code, item, fragment, distance."""
    if collection is None:
        return []
    count = collection.count()
    if count == 0:
        return []

    settings = get_settings()
    top_k = top_k or settings.rag.top_k
    embed = embedder or embeddings.embed_text

    result = collection.query(
        query_embeddings=[embed(query)],
        n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
    )
    hits: list[dict] = []
    for doc, meta, dist in zip(
        result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        hits.append(
            {
                "chunk_id": meta.get("chunk_id"),
                "document_id": meta.get("document_id"),
                "code": meta.get("code"),
                "section": meta.get("section"),
                "item": meta.get("item"),
                "fragment": doc,
                "distance": dist,
            }
        )
    return hits
