"""Проверка RAG: подбор нормативных пунктов и генерация обоснования.

Стенд прогоняет тестовое предписание (data/testdata/rag_test_prescription.json)
по реальной загруженной нормативной базе: ChromaDB + Ollama. По каждому пункту
показывает найденные фрагменты (top-k), выбранные LLM ссылки, сверяет их с
ожидаемым кодом нормы и печатает сформированное обоснование.

Примеры:
    python scripts/check_rag_justification.py
    python scripts/check_rag_justification.py --top-k 8
    python scripts/check_rag_justification.py --no-llm          # только поиск
    python scripts/check_rag_justification.py --apply            # записать в БД
    python scripts/check_rag_justification.py --report report.json

По умолчанию стенд ничего не пишет в БД. --apply сохраняет предложенные
ссылки (suggested) и текст обоснования в prescriptions.justification.

Требуется запущенная Ollama с моделями bge-m3 и LLM из config.yaml
(по умолчанию qwen2.5:7b; тег должен совпадать с `ollama list`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402
from sqlalchemy import select  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.db.engine import get_session_factory  # noqa: E402
from src.db.models import (  # noqa: E402
    NormDoc,
    Prescription,
    PrescriptionItem,
    User,
    UserRole,
)
from src.llm.client import OllamaClient  # noqa: E402
from src.observability import configure_logging  # noqa: E402
from src.prescriptions.service import apply_generated_justification  # noqa: E402
from src.rag import recommend as rag_recommend  # noqa: E402
from src.rag.search import get_collection, search_topk  # noqa: E402
from src.rag.testdata import load_test_prescription  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_OLLAMA = 2
EXIT_NO_NORMS = 3


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _find_prescription(db, args) -> Prescription | None:
    if args.prescription_id is not None:
        return db.get(Prescription, args.prescription_id)
    doc_number = args.doc_number or load_test_prescription().meta_for("internal").doc_number
    return db.scalar(
        select(Prescription)
        .where(Prescription.doc_number == doc_number)
        .order_by(Prescription.id.desc())
    )


def _preflight() -> str | None:
    """Проверить доступность Ollama; вернуть текст ошибки или None."""
    settings = get_settings()
    base = settings.ollama.base_url.rstrip("/")
    try:
        response = requests.get(f"{base}/api/tags", timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        return (
            f"Ollama недоступен по адресу {base}: {exc}. "
            "Запустите `ollama serve` и подтяните модели: "
            f"`ollama pull {settings.ollama.embed_model}`, "
            f"`ollama pull {settings.ollama.llm_model}`."
        )
    return None


def _expected_by_text(fixture) -> dict[str, list[str]]:
    return {_normalize(item.text): item.expect for item in fixture.items}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проверка RAG: подбор норм и генерация обоснования"
    )
    parser.add_argument("--prescription-id", type=int, default=None)
    parser.add_argument("--doc-number", default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="только векторный поиск, без выбора фрагментов моделью",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="сохранить предложенные ссылки (suggested) и обоснование в БД",
    )
    parser.add_argument("--report", default=None, help="выгрузить отчёт в JSON")
    parser.add_argument("--json", action="store_true", help="печатать отчёт в JSON")
    args = parser.parse_args()

    configure_logging("check_rag_justification")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    settings = get_settings()
    # Ollama нужен всегда: даже в режиме --no-llm эмбеддинги запроса
    # считает bge-m3 через Ollama.
    problem = _preflight()
    if problem:
        print(f"ОШИБКА: {problem}", file=sys.stderr)
        return EXIT_NO_OLLAMA

    try:
        collection = get_collection()
    except Exception as exc:  # pragma: no cover - зависит от окружения
        print(f"ОШИБКА: не удалось открыть коллекцию ChromaDB: {exc}", file=sys.stderr)
        return EXIT_NO_NORMS
    chunk_count = collection.count()
    if chunk_count == 0:
        print(
            "ОШИБКА: нормативная база пуста. Загрузите документы: "
            "python scripts/load_norms.py docs/priors/ --pattern \"*.pdf\"",
            file=sys.stderr,
        )
        return EXIT_NO_NORMS

    fixture = load_test_prescription()
    expectations = _expected_by_text(fixture)

    db = get_session_factory()()
    try:
        prescription = _find_prescription(db, args)
        if prescription is None:
            print(
                "ОШИБКА: предписание не найдено. Создайте тестовое: "
                "python scripts/seed_test_prescription.py",
                file=sys.stderr,
            )
            return EXIT_ERROR
        items = db.scalars(
            select(PrescriptionItem)
            .where(PrescriptionItem.prescription_id == prescription.id)
            .order_by(PrescriptionItem.item_number, PrescriptionItem.id)
        ).all()
        if not items:
            print("ОШИБКА: у предписания нет пунктов.", file=sys.stderr)
            return EXIT_ERROR

        known_codes = set(db.scalars(select(NormDoc.code)).all())
        client = OllamaClient()
        top_k = args.top_k or settings.rag.top_k

        results: list[dict] = []
        all_refs: list[rag_recommend.ProposedNormRef] = []
        raw_refs: list[list[rag_recommend.ProposedNormRef]] = []
        item_justifications: dict[int, str] = {}
        for item in items:
            started = time.monotonic()
            hits = search_topk(collection, item.text, top_k)
            expect = expectations.get(_normalize(item.text), [])
            entry: dict = {
                "item_id": item.id,
                "item_number": item.item_number,
                "text": item.text,
                "expected": expect,
                "hits": [
                    {
                        "code": hit.get("code"),
                        "item": hit.get("item"),
                        "distance": hit.get("distance"),
                        "fragment": (hit.get("fragment") or "")[:400],
                    }
                    for hit in hits
                ],
            }

            if args.no_llm:
                entry["refs"] = []
                entry["justification"] = ""
                entry["retrieval_hit"] = any(
                    hit.get("code") in expect for hit in hits
                ) if expect else None
                raw_refs.append([])
            else:
                refs, justification = rag_recommend.generate_justification(
                    client,
                    collection,
                    item.text,
                    db=db,
                    top_k=top_k,
                    prescription_id=prescription.id,
                )
                entry["refs"] = [
                    {
                        "doc_code": ref.doc_code,
                        "item": ref.item,
                        "reason": ref.reason,
                        "in_base": ref.doc_code in known_codes,
                    }
                    for ref in refs
                ]
                entry["justification"] = justification
                entry["retrieval_hit"] = any(
                    ref.doc_code in expect for ref in refs
                ) if expect else None
                all_refs.extend(refs)
                raw_refs.append(refs)
                if justification:
                    item_justifications[item.id] = justification

            entry["duration_ms"] = int((time.monotonic() - started) * 1000)
            results.append(entry)

        combined_justification = rag_recommend.build_justification(all_refs)
        applied = False
        if args.apply:
            saved_refs = 0
            for item, refs in zip(items, raw_refs, strict=False):
                saved_refs += rag_recommend.save_proposed_refs(
                    db, prescription, refs, item_id=item.id
                )
            actor = db.scalar(
                select(User)
                .where(
                    User.role.in_([UserRole.INSPECTOR, UserRole.ADMIN]),
                    User.is_active.is_(True),
                )
                .order_by(User.id)
            )
            if actor is None:
                print(
                    "ВНИМАНИЕ: нет активного инспектора/администратора — "
                    "обоснование в prescriptions.justification не записано.",
                    file=sys.stderr,
                )
            else:
                applied = apply_generated_justification(
                    db,
                    prescription,
                    combined_justification,
                    actor_user_id=actor.id,
                    item_justifications=item_justifications,
                )
            print(
                f"Сохранено предложенных ссылок: {saved_refs}; "
                f"обоснование записано: {'да' if applied else 'нет'}."
            )

        matched = sum(
            1
            for entry in results
            if entry["expected"] and entry["retrieval_hit"]
        )
        expected_items = sum(1 for entry in results if entry["expected"])
        with_refs = sum(1 for entry in results if entry["refs"])
        control_entries = [
            entry
            for entry in results
            if not entry["expected"]
        ]
        control_clean = all(not entry["refs"] for entry in control_entries)
        foreign = [
            ref
            for entry in results
            for ref in entry["refs"]
            if not ref["in_base"]
        ]

        report = {
            "prescription": {
                "id": prescription.id,
                "doc_number": prescription.doc_number,
                "doc_date": prescription.doc_date.isoformat(),
                "type": prescription.prescription_type.value,
                "status": prescription.status.value,
            },
            "base": {
                "collection": settings.rag.collection,
                "chunks": chunk_count,
                "documents": len(known_codes),
                "top_k": top_k,
                "llm": "выключен (--no-llm)" if args.no_llm else settings.ollama.llm_model,
                "embed_model": settings.ollama.embed_model,
            },
            "items": results,
            "summary": {
                "items": len(results),
                "items_with_refs": with_refs,
                "items_with_justification": len(item_justifications),
                "expected_items": expected_items,
                "matched": matched,
                "match_rate": (
                    round(matched / expected_items, 3) if expected_items else None
                ),
                "control_items": len(control_entries),
                "control_clean": control_clean,
                "refs_outside_base": len(foreign),
                "justification_saved": applied,
            },
            "justification": combined_justification,
        }

        if args.report:
            Path(args.report).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"Отчёт: {args.report}")

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_report(report)
        return EXIT_OK
    finally:
        db.close()


def _print_report(report: dict) -> None:
    base = report["base"]
    presc = report["prescription"]
    print("=" * 78)
    print(f"Проверка RAG: предписание id={presc['id']} № {presc['doc_number']} "
          f"({presc['type']}, {presc['status']})")
    print(
        f"База: {base['collection']} — документов {base['documents']}, "
        f"чанков {base['chunks']}, top_k={base['top_k']}, "
        f"эмбеддинги {base['embed_model']}, LLM {base['llm']}"
    )
    print("=" * 78)
    for entry in report["items"]:
        print(f"\nПункт {entry['item_number']}. {entry['text']}")
        print(f"  Найдено фрагментов: {len(entry['hits'])}")
        for index, hit in enumerate(entry["hits"]):
            point = f" п.{hit['item']}" if hit.get("item") else ""
            distance = hit.get("distance")
            distance_text = f"{distance:.3f}" if isinstance(distance, float) else "—"
            print(
                f"    [{index}] {hit['code']}{point} (d={distance_text}) "
                f"{hit['fragment'][:120].replace(chr(10), ' ')}…"
            )
        if entry["refs"]:
            print("  Выбрано моделью:")
            for ref in entry["refs"]:
                point = f" п.{ref['item']}" if ref.get("item") else ""
                mark = "" if ref["in_base"] else "  ⚠ НЕТ В БАЗЕ"
                print(f"    - {ref['doc_code']}{point}{mark}")
                if ref.get("reason"):
                    print(f"      причина: {ref['reason']}")
        else:
            print("  Выбрано моделью: ничего (подходящий пункт не найден)")
        if entry["expected"]:
            verdict = "СОВПАДЕНИЕ" if entry["retrieval_hit"] else "НЕ СОВПАЛО"
            print(f"  Ожидалась норма: {'; '.join(entry['expected'])} → {verdict}")
        if entry["justification"]:
            print("  Обоснование:")
            for line in entry["justification"].splitlines():
                print(f"    {line}")
    summary = report["summary"]
    print("\n" + "=" * 78)
    print("Итог:")
    print(f"  пунктов: {summary['items']}, с предложенными ссылками: "
          f"{summary['items_with_refs']}, с обоснованием: "
          f"{summary['items_with_justification']}")
    if summary["expected_items"]:
        rate = summary["match_rate"]
        percent = f"{rate * 100:.0f}%" if rate is not None else "—"
        print(f"  совпадение с ожидаемой нормой: {summary['matched']}/"
              f"{summary['expected_items']} ({percent})")
    print(f"  контрольных пунктов: {summary['control_items']}, "
          f"без выдуманных ссылок: {'да' if summary['control_clean'] else 'НЕТ'}")
    print(f"  ссылок вне загруженной базы: {summary['refs_outside_base']}")
    print("=" * 78)


if __name__ == "__main__":
    raise SystemExit(main())
