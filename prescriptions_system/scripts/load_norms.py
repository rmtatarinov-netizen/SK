"""Загрузка нормативных документов в RAG-базу (Шаг 7).

Примеры:
    python scripts/load_norms.py docs/sp_63.pdf --code "СП 63.13330.2018" --title "Бетонные конструкции"
    python scripts/load_norms.py docs/ --pattern "*.pdf"
    python scripts/load_norms.py doc.txt --code "ГОСТ Р 50211-92" --status "действующий"

Требуется запущенный Ollama с моделью bge-m3 (scripts/pull_models.ps1).
Повторная загрузка того же --code заменяет чанки (идемпотентно).
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from pathlib import Path
from tqdm import tqdm


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.engine import get_session_factory  # noqa: E402
from src.rag.ingest_norms import ingest_document  # noqa: E402
from src.rag.search import get_collection  # noqa: E402


def _collect_inputs(paths: list[str], pattern: str | None) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_dir():
            if not pattern:
                print(f"Для каталога {raw} укажите --pattern", file=sys.stderr)
                continue
            files.extend(Path(p) for p in sorted(glob.glob(str(candidate / pattern))))
        else:
            files.append(candidate)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest norm docs into RAG")
    parser.add_argument("paths", nargs="+", help="файлы или каталоги документов")
    parser.add_argument("--pattern", default=None, help="glob для каталогов, напр. *.pdf")
    parser.add_argument("--code", default=None, help="код документа (для одного файла)")
    parser.add_argument("--title", default=None)
    parser.add_argument("--edition", default=None)
    parser.add_argument("--status", default="действующий")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    files = _collect_inputs(args.paths, args.pattern)
    if not files:
        print("Нет входных файлов.", file=sys.stderr)
        return 2
    if len(files) > 1 and args.code:
        print("--code применим только к одному файлу.", file=sys.stderr)
        return 2

    db = get_session_factory()()
    try:
        collection = get_collection()
    except Exception as exc:
        print(f"Не удалось открыть ChromaDB: {exc}", file=sys.stderr)
        db.close()
        return 2

    summary: list[dict] = []
    failures = 0
    try:
        for path in tqdm(files, desc="Загрузка документов"):
            try:
                if not path.exists():
                    raise FileNotFoundError(path)
                doc = ingest_document(
                    db,
                    collection,
                    path,
                    code=args.code,
                    title=args.title,
                    edition=args.edition,
                    doc_status=args.status,
                )
                summary.append(
                    {
                        "file": path.name,
                        "code": doc.code,
                        "chunks": collection.count(),
                    }
                )
            except Exception as exc:
                failures += 1
                db.rollback()
                summary.append({"file": path.name, "error": str(exc)})
                logging.getLogger(__name__).exception("ingest failed")
    finally:
        db.close()

    print(json.dumps({"ingested": len(files) - failures, "failed": failures, "details": summary}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
