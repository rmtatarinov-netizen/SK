"""Пересборка векторов ChromaDB из сохранённых чанков БД (без парсинга PDF).

Нужна, если каталог HNSW-индекса повреждён/потерян: тексты чанков и их
метаданные остаются в SQLite, поэтому достаточно заново посчитать эмбеддинги
bge-m3 через Ollama и разложить их по тем же id ``<document_id>:<chunk_index>``.

Пример:
    python scripts/rebuild_chroma.py
    python scripts/rebuild_chroma.py --resume   # продолжить прерванный прогон

Каждые 1024 записи коллекция переоткрывается «свежим» клиентом: обрыв
записи индекса виден сразу, а не после часа работы.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import requests
from sqlalchemy import select
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import get_settings  # noqa: E402
from src.db.engine import get_session_factory  # noqa: E402
from src.db.models import NormChunk, NormDoc  # noqa: E402
from src.rag.search import get_collection  # noqa: E402

logger = logging.getLogger("rebuild_chroma")

PHASE_RECORDS = 1024


def _reset_chroma_clients() -> None:
    """Полностью закрыть per-process кеш Chroma, чтобы следующий
    get_collection() открыл файлы с диска, а не из памяти старого клиента."""
    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:  # pragma: no cover - зависит от версии chromadb
        pass


def _ollama_embed_batch(
    session: requests.Session, base_url: str, model: str, texts: list[str], timeout: int
) -> list[list[float]]:
    response = session.post(
        f"{base_url}/api/embed",
        json={"model": model, "input": texts},
        timeout=timeout,
    )
    if response.status_code == 404:
        raise FileNotFoundError("/api/embed")
    response.raise_for_status()
    vectors = response.json().get("embeddings")
    if not vectors or len(vectors) != len(texts):
        raise RuntimeError(f"Ollama вернула {len(vectors or [])} векторов на {len(texts)} текстов")
    return vectors


def _ollama_embed_one(
    session: requests.Session, base_url: str, model: str, text: str, timeout: int
) -> list[float]:
    response = session.post(
        f"{base_url}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=timeout,
    )
    response.raise_for_status()
    vector = response.json().get("embedding")
    if not vector:
        raise RuntimeError("Ollama вернула пустой embedding")
    return vector


def main() -> int:
    parser = argparse.ArgumentParser(description="Пересборка ChromaDB из чанков БД")
    parser.add_argument("--batch", type=int, default=16, help="текстов на один запрос")
    parser.add_argument("--chunk-batch", type=int, default=64, help="записей на collection.add")
    parser.add_argument("--timeout", type=int, default=600, help="таймаут запроса, сек")
    parser.add_argument("--limit", type=int, default=0, help="только первые N чанков (проверка)")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="продолжить прерванную пересборку: пропустить id, уже лежащие в коллекции",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = get_settings()
    base_url = settings.ollama.base_url.rstrip("/")
    model = settings.ollama.embed_model

    db = get_session_factory()()
    collection = get_collection()
    before = collection.count()
    if before and not args.resume:
        print(
            f"Коллекция {settings.rag.collection!r} уже содержит {before} записей; "
            "пересборка ожидает пустую коллекцию (или --resume).",
            file=sys.stderr,
        )
        db.close()
        return 2

    rows = db.execute(
        select(
            NormChunk.id,
            NormChunk.document_id,
            NormChunk.chunk_index,
            NormChunk.section,
            NormChunk.item,
            NormChunk.text,
            NormDoc.code,
        )
        .join(NormDoc, NormDoc.id == NormChunk.document_id)
        .order_by(NormDoc.id, NormChunk.chunk_index)
    ).all()
    if before:
        existing = set(collection.get(include=[])["ids"])
        rows = [r for r in rows if f"{r.document_id}:{r.chunk_index}" not in existing]
        print(f"Пропущено уже векторизованных: {before}")
    if args.limit:
        rows = rows[: args.limit]
    total = len(rows)
    print(f"Чанков к векторизации: {total} (модель {model})")

    ids = [f"{r.document_id}:{r.chunk_index}" for r in rows]
    if len(set(ids)) != len(ids):
        print("ОШИБКА: дублирующиеся id чанков", file=sys.stderr)
        db.close()
        return 2

    started = time.monotonic()
    pending_ids: list[str] = []
    pending_vecs: list[list[float]] = []
    pending_docs: list[str] = []
    pending_meta: list[dict] = []

    def flush() -> None:
        if not pending_ids:
            return
        collection.add(
            ids=list(pending_ids),
            embeddings=list(pending_vecs),
            documents=list(pending_docs),
            metadatas=list(pending_meta),
        )
        pending_ids.clear()
        pending_vecs.clear()
        pending_docs.clear()
        pending_meta.clear()

    http = requests.Session()
    batch_mode = True
    try:
        processed = 0
        phase_next = PHASE_RECORDS
        with tqdm(total=total, desc="Эмбеддинги", unit="чанк") as bar:
            for start in range(0, total, args.batch):
                batch = rows[start : start + args.batch]
                texts = [r.text for r in batch]
                if batch_mode:
                    try:
                        vectors = _ollama_embed_batch(http, base_url, model, texts, args.timeout)
                    except FileNotFoundError:
                        logger.warning("Ollama не поддерживает /api/embed, переключаюсь на /api/embeddings")
                        batch_mode = False
                        vectors = [
                            _ollama_embed_one(http, base_url, model, t, args.timeout)
                            for t in texts
                        ]
                else:
                    vectors = [
                        _ollama_embed_one(http, base_url, model, t, args.timeout)
                        for t in texts
                    ]
                for row, vector in zip(batch, vectors):
                    pending_ids.append(f"{row.document_id}:{row.chunk_index}")
                    pending_vecs.append(vector)
                    pending_docs.append(row.text)
                    pending_meta.append(
                        {
                            "document_id": row.document_id,
                            "chunk_id": row.id,
                            "chunk_index": row.chunk_index,
                            "code": row.code,
                            "section": row.section or "",
                            "item": row.item or "",
                        }
                    )
                processed += len(batch)
                if len(pending_ids) >= args.chunk_batch:
                    flush()
                if processed >= phase_next:
                    flush()
                    logger.info(
                        "Фаза %s: записано, проверяю свежим клиентом...", processed
                    )
                    _reset_chroma_clients()
                    collection = get_collection()
                    fresh_count = collection.count()
                    if fresh_count != before + processed:
                        raise RuntimeError(
                            f"на этапе {processed} свежий клиент видит только {fresh_count} векторов"
                        )
                    phase_next += PHASE_RECORDS
                bar.update(len(batch))
            flush()
    except Exception:
        logger.exception("Пересборка прервана")
        db.close()
        return 1
    finally:
        http.close()
        db.close()

    elapsed = int(time.monotonic() - started)
    _reset_chroma_clients()
    verify = get_collection()
    count = verify.count()
    print(f"Готово: {count} векторов за {elapsed} с")
    if count == before + total and total:
        from src.rag.embeddings import embed_text

        hits = verify.query(query_embeddings=[embed_text("нарушение")], n_results=1)
        print(f"Контрольный запрос вернул id: {hits['ids'][0]}")
    return 0 if count == before + total else 1


if __name__ == "__main__":
    raise SystemExit(main())
