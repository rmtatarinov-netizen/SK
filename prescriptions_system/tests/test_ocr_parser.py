"""Тесты парсера fast-полей (Шаг 5)."""

from __future__ import annotations

from datetime import date

from src.ocr.parsers import extract_norm_refs, parse_date, parse_fields

SAMPLE = """ПРЕДПИСАНИЕ № П-14/2026 от 01.09.2026
Объект: Склад №3
Подрядчик: ООО СтройМонтаж
Выявлены нарушения: Отсутствие защитного слоя бетона в плите перекрытия
1. Обеспечить защитный слой бетона в пролете 2
2. Представить акт скрытых работ
Основание: СП 63.13330.2018, СНиП 12-03-2001
Ответственный: Иванов И.И.
Срок устранения: 30.09.2026"""


def test_parse_fields_full_document():
    f = parse_fields(SAMPLE)
    assert f.doc_number == "П-14/2026"
    assert f.doc_date == date(2026, 9, 1)
    assert f.deadline == date(2026, 9, 30)
    assert f.object_name == "Склад №3"
    assert f.contractor_name == "ООО СтройМонтаж"
    assert f.responsible is not None and f.responsible.startswith("Иванов")
    assert f.description is not None and "защитного слоя" in f.description
    assert len(f.items) == 2
    assert f.norm_refs == ["СП 63.13330.2018", "СНиП 12-03-2001"]


def test_parse_fields_minimal():
    f = parse_fields("Предписание № 55 от 14.03.2026. Устранить течь кровли.")
    assert f.doc_number == "55"
    assert f.doc_date == date(2026, 3, 14)
    assert f.deadline is None
    assert f.items == []


def test_parse_fields_empty():
    f = parse_fields("")
    assert f.doc_number is None
    assert f.doc_date is None
    assert f.norm_refs == []


def test_parse_date_formats():
    assert parse_date("01.09.2026") == date(2026, 9, 1)
    assert parse_date("2026-09-01") == date(2026, 9, 1)
    assert parse_date("1/9/26") == date(2026, 9, 1)
    assert parse_date("31.02.2026") is None
    assert parse_date("не дата") is None


def test_extract_norm_refs_dedup_and_kinds():
    text = "СП 63.13330.2018 и СП 63.13330.2018 снова, ГОСТ Р 50211-92, СТО 1-2.3, ТУ 123-45"
    refs = extract_norm_refs(text)
    assert "СП 63.13330.2018" in refs
    assert refs.count("СП 63.13330.2018") == 1
    assert "ГОСТ Р 50211-92" in refs or any(r.startswith("ГОСТ") for r in refs)
    assert any(r.startswith("СТО") for r in refs)
    assert any(r.startswith("ТУ") for r in refs)
