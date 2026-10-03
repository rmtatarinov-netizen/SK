"""Импорт нормативных документов: PDF/DOCX/TXT → чанки → ChromaDB + БД (Шаг 7).

Идемпотентность: повторная загрузка того же кода документа заменяет чанки
(старые векторы удаляются из коллекции). Исходники хранятся в
storage/norm_sources и не удаляются.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import fitz
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import NormChunk, NormChunkEmbedding, NormDoc
from src.rag import embeddings
from src.rag.chunking import chunk_text

_DOCX_TEXT = re.compile(r"<w:t[ >](?:[^<]*<[^>]*>)*", re.IGNORECASE)


def extract_text(path: Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        doc = fitz.open(str(path))
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".docx":
        return _docx_text(path)
    raise ValueError(f"Неподдерживаемый формат: {suffix} (нужен pdf/docx/txt/md)")


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except KeyError as exc:  # pragma: no cover - битый docx
        raise ValueError("Повреждённый DOCX: нет word/document.xml") from exc
    paragraphs: list[str] = []
    for p_xml in re.findall(r"<w:p\b.*?</w:p>", xml, re.DOTALL):
        try:
            root = ElementTree.fromstring(p_xml)
            texts = [
                node.text or ""
                for node in root.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                )
            ]
            paragraphs.append("".join(texts))
        except ElementTree.ParseError:
            continue
    return "\n".join(paragraphs)


def store_source(path: Path) -> Path:
    settings = get_settings()
    target_dir = settings.resolve_path(settings.storage.storage) / "norm_sources"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if not target.exists() or target.stat().st_size != path.stat().st_size:
        import shutil

        shutil.copy2(path, target)
    return target


def delete_document_chunks(db: Session, collection, document_id: int) -> None:
    if collection is not None:
        collection.delete(where={"document_id": document_id})
    db.execute(delete(NormChunkEmbedding).where(
        NormChunkEmbedding.chunk_id.in_(
            select(NormChunk.id).where(NormChunk.document_id == document_id)
        )
    ))
    db.execute(delete(NormChunk).where(NormChunk.document_id == document_id))


def ingest_document(
    db: Session,
    collection,
    path: Path,
    *,
    code: str | None = None,
    title: str | None = None,
    edition: str | None = None,
    doc_status: str = "действующий",
) -> NormDoc:
    """Загрузить/перезагрузить один документ. Возвращает NormDoc."""
    path = Path(path)
    code = (code or path.stem).strip()
    if not code:
        raise ValueError("Не определён код документа (code или имя файла)")

    text = extract_text(path)
    if not text.strip():
        raise ValueError(f"Из документа не извлечён текст: {path.name}")

    settings = get_settings()
    rag = settings.rag
    chunks = chunk_text(
        text,
        chunk_size_tokens=rag.chunk_size_tokens,
        overlap_tokens=rag.chunk_overlap_tokens,
    )
    stored = store_source(path)

    doc = db.scalar(select(NormDoc).where(NormDoc.code == code))
    if doc is not None:
        delete_document_chunks(db, collection, doc.id)
        doc.title = title or doc.title
        doc.edition = edition or doc.edition
        doc.status = doc_status
        doc.source_file = str(stored)
    else:
        doc = NormDoc(
            code=code,
            title=title or code,
            edition=edition,
            status=doc_status,
            source_file=str(stored),
        )
        db.add(doc)
    db.flush()

    rows: list[NormChunk] = []
    for chunk in chunks:
        row = NormChunk(
            document_id=doc.id,
            section=chunk.section,
            item=chunk.item,
            text=chunk.text,
            chunk_index=chunk.chunk_index,
            start_char=chunk.start_char,
            end_char=chunk.end_char,
        )
        db.add(row)
        rows.append(row)
    db.flush()

    if collection is not None and rows:
        vectors = embeddings.embed_texts([row.text for row in rows])
        collection.add(
            ids=[f"{doc.id}:{row.chunk_index}" for row in rows],
            embeddings=vectors,
            documents=[row.text for row in rows],
            metadatas=[
                {
                    "document_id": doc.id,
                    "chunk_id": row.id,
                    "chunk_index": row.chunk_index,
                    "code": doc.code,
                    "section": row.section or "",
                    "item": row.item or "",
                }
                for row in rows
            ],
        )
        embed_model = get_settings().ollama.embed_model
        for row in rows:
            db.add(
                NormChunkEmbedding(
                    chunk_id=row.id,
                    chroma_id=f"{doc.id}:{row.chunk_index}",
                    model=embed_model,
                )
            )

    db.commit()
    db.refresh(doc)
    return doc
