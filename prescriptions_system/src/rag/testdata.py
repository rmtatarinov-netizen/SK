"""Материалы проверочного стенда RAG (тестовое предписание).

Единый источник данных для scripts/seed_test_prescription.py,
scripts/check_rag_justification.py и tests/test_rag_justification.py —
файл data/testdata/rag_test_prescription.json.

Коды документов в expect-полях совпадают с кодами norm_docs (имя
загруженного файла). RAG не выдаёт ссылку, отсутствующую в norm_docs,
поэтому расхождение кода означает ошибку фикстуры, а не модели.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TESTDATA_DIR = Path(__file__).resolve().parents[2] / "data" / "testdata"
TEST_PRESCRIPTION_PATH = TESTDATA_DIR / "rag_test_prescription.json"


@dataclass(frozen=True)
class TestItem:
    """Пункт тестового предписания с ожидаемым кодом нормы."""

    number: int
    text: str
    expect: list[str] = field(default_factory=list)
    expect_items: list[str] = field(default_factory=list)
    control: bool = False
    control_note: str | None = None

    @property
    def is_control(self) -> bool:
        return self.control or not self.expect


@dataclass(frozen=True)
class TestPrescriptionMeta:
    doc_number: str
    doc_date: date
    deadline: date | None
    object_name: str
    contractor_name: str
    description: str


@dataclass(frozen=True)
class TestPrescription:
    meta: dict[str, TestPrescriptionMeta]
    items: list[TestItem]
    purpose: str = ""

    def meta_for(self, kind: str) -> TestPrescriptionMeta:
        if kind not in self.meta:
            raise KeyError(f"В фикстуре нет блока meta.{kind}")
        return self.meta[kind]

    def working_items(self) -> list[TestItem]:
        """Пункты с ожидаемой нормой (без контрольного)."""
        return [item for item in self.items if not item.is_control]

    def control_items(self) -> list[TestItem]:
        return [item for item in self.items if item.is_control]


def load_test_prescription(path: Path | str | None = None) -> TestPrescription:
    target = Path(path) if path else TEST_PRESCRIPTION_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"Файл тестового предписания не найден: {target}. "
            "Создайте data/testdata/rag_test_prescription.json"
        )
    raw = json.loads(target.read_text(encoding="utf-8"))
    meta = {
        kind: TestPrescriptionMeta(
            doc_number=block["doc_number"],
            doc_date=date.fromisoformat(block["doc_date"]),
            deadline=(
                date.fromisoformat(block["deadline"]) if block.get("deadline") else None
            ),
            object_name=block["object_name"],
            contractor_name=block["contractor_name"],
            description=block["description"],
        )
        for kind, block in (raw.get("meta") or {}).items()
    }
    items = [
        TestItem(
            number=int(item["number"]),
            text=item["text"],
            expect=list(item.get("expect") or []),
            expect_items=list(item.get("expect_items") or []),
            control=bool(item.get("control", False)),
            control_note=item.get("control_note"),
        )
        for item in (raw.get("items") or [])
    ]
    if not items:
        raise ValueError(f"В файле {target} нет ни одного пункта предписания")
    return TestPrescription(
        meta=meta, items=items, purpose=raw.get("purpose", "")
    )
