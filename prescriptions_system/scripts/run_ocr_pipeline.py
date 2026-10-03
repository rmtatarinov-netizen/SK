"""Запуск OCR-пайплайна (Шаг 5): вручную или по расписанию.

Примеры:
    python scripts/run_ocr_pipeline.py
    python scripts/run_ocr_pipeline.py --object-id 1 --contractor-id 2
    python scripts/run_ocr_pipeline.py --inbox data/prescriptions/inbox/customer

Повторный запуск безопасен: обработанные файлы пропускаются по SHA-256.
Код возврата 0 — ошибок нет; 1 — есть файлы со статусом error/incomplete.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from src.db.engine import get_session_factory  # noqa: E402
from src.db.models import Contractor, Object  # noqa: E402
from src.observability import configure_logging  # noqa: E402
from src.ocr.pipeline import run_pipeline  # noqa: E402


def _resolve_by_name(db, model, name: str):
    return db.scalar(select(model).where(model.name == name))


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR pipeline for customer prescriptions")
    parser.add_argument("--inbox", default=None, help="каталог inbox (по умолчанию из config.yaml)")
    parser.add_argument("--object-id", type=int, default=None)
    parser.add_argument("--contractor-id", type=int, default=None)
    parser.add_argument("--object", dest="object_name", default=None, help="именование объекта из справочника")
    parser.add_argument("--contractor", dest="contractor_name", default=None)
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="после OCR вызвать LLM-извлечение (Ollama, Шаг 6)",
    )
    args = parser.parse_args()

    configure_logging("ocr_pipeline")

    db = get_session_factory()()
    try:
        object_id = args.object_id
        if object_id is None and args.object_name:
            found = _resolve_by_name(db, Object, args.object_name)
            if found is None:
                print(f"Объект не найден в справочнике: {args.object_name}", file=sys.stderr)
                return 2
            object_id = found.id
        contractor_id = args.contractor_id
        if contractor_id is None and args.contractor_name:
            found = _resolve_by_name(db, Contractor, args.contractor_name)
            if found is None:
                print(f"Подрядчик не найден в справочнике: {args.contractor_name}", file=sys.stderr)
                return 2
            contractor_id = found.id

        llm_client = None
        if args.use_llm:
            from src.llm.client import OllamaClient

            llm_client = OllamaClient()

        summary = run_pipeline(
            db,
            inbox_dir=args.inbox,
            object_id=object_id,
            contractor_id=contractor_id,
            llm_client=llm_client,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        bad = summary["counters"].get("error", 0) + summary["counters"].get("incomplete", 0)
        return 1 if bad else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
