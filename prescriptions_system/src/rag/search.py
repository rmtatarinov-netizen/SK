"""Хранилище и поиск по чанкам нормативных документов (Шаг 7).

ChromaDB persistent; векторы считаются локальной bge-m3. RAG ничего не
подтверждает: пустая база или отсутствие релевантных фрагментов — пустой
результат. Ссылка, отсутствующая в загруженной базе, не выдаётся.
"""

from __future__ import annotations

from collections.abc import Callable

from src.config import get_settings
from src.rag import embeddings


def get_collection():
    """Открыть persistent-коллекцию нормативных чанков (cosine)."""
    import chromadb

    settings = get_settings()
    path = settings.resolve_path(settings.rag.persist_directory)
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(path))
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
