"""Черновое извлечение полей из OCR-текста регулярными выражениями (Шаг 5).

Регулярки дают только черновые fast-поля (номер, дата, срок, названия
объекта/подрядчика, явные нормативные ссылки, нумерованные пункты).
Финальные значения и нормативные ссылки определяет LLM (Шаг 6) и
подтверждает инспектор (§1.6: OCR/LLM — не подтверждение).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

_DATE_PATTERNS = ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d")

RE_DOC_NUMBER = re.compile(
    r"предписани[ея]\s*(?:№|No\.?|N)?\s*[:\s]*"
    r"([А-ЯA-Z][А-Яа-яA-Za-z0-9\-/_.]{1,59})",
    re.IGNORECASE,
)
RE_ANY_NUMBER = re.compile(r"№\s*([А-ЯA-Z0-9][А-Яа-яA-Za-z0-9\-/_.]{1,59})")
RE_FROM_DATE = re.compile(
    r"от\s*[:\s]*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", re.IGNORECASE
)
RE_ANY_DATE = re.compile(
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})"
)
RE_DEADLINE = re.compile(
    r"срок[^\n]{0,60}?:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
# Значение обрывается на переносе строки, пунктуации перед следующим
# ключевым словом или на конце текста.
_STOP = (
    r"(?=[\n,.;]|\s+(?:объект|подрядчик|исполнитель|генподрядчик|заказчик|"
    r"срок|ответствен|требован|основан|предписани)|$)"
)
RE_OBJECT = re.compile(r"объект[^\n:]{0,30}:\s*([^:]{3,120}?)\s*" + _STOP, re.IGNORECASE)
RE_CONTRACTOR = re.compile(
    r"(?:подрядчик|исполнитель)[^\n:]{0,30}:\s*([^:]{3,120}?)\s*" + _STOP,
    re.IGNORECASE,
)
RE_RESPONSIBLE = re.compile(
    r"ответствен[^\n:]{0,30}:\s*([^:]{3,120}?)\s*" + _STOP, re.IGNORECASE
)
RE_DESCRIPTION = re.compile(
    r"(?:нарушени[ея]|выявлено|описани[ея])[^\n:]{0,30}:\s*(.+?)"
    r"(?=\n\s*\d{1,2}[.)]\s|\n\s*\n|Срок|Требования|Основание|Ответствен|Приложение|$)",
    re.IGNORECASE | re.DOTALL,
)
RE_NORM_REF = re.compile(
    r"\b(СНиП|СП|ГОСТ\s*Р?|СТО|ТУ)\s*(\d[0-9А-Яа-яA-Za-z.\-]{1,35})",
)
RE_ITEM = re.compile(r"^\s*(\d{1,2})[.)\s]\s+(.+)$", re.MULTILINE)


@dataclass
class FastFields:
    doc_number: str | None = None
    doc_date: date | None = None
    deadline: date | None = None
    object_name: str | None = None
    contractor_name: str | None = None
    responsible: str | None = None
    description: str | None = None
    items: list[str] = field(default_factory=list)
    norm_refs: list[str] = field(default_factory=list)


def parse_date(raw: str) -> date | None:
    candidate = raw.strip()
    if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2}", candidate):
        day, month, year = re.split(r"[./-]", candidate)
        candidate = f"{day}.{month}.20{year}"
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip("«»\"'").rstrip(".,;: \t").strip()
    return cleaned or None


def extract_norm_refs(text: str) -> list[str]:
    refs: list[str] = []
    for prefix, number in RE_NORM_REF.findall(text):
        ref = _clean(f"{prefix} {number}".replace("  ", " "))
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def extract_items(text: str) -> list[str]:
    items: list[str] = []
    for m in RE_ITEM.finditer(text):
        value = _clean(m.group(2))
        if value and len(value) > 3:
            items.append(value)
    return items


def parse_fields(text: str) -> FastFields:
    fields = FastFields()
    if not text or not text.strip():
        return fields

    m = RE_DOC_NUMBER.search(text) or RE_ANY_NUMBER.search(text)
    fields.doc_number = _clean(m.group(1)) if m else None

    m = RE_FROM_DATE.search(text)
    if m:
        fields.doc_date = parse_date(m.group(1))
    if fields.doc_date is None:
        m_any = RE_ANY_DATE.search(text)
        if m_any:
            fields.doc_date = parse_date(m_any.group(1))

    m = RE_DEADLINE.search(text)
    if m:
        fields.deadline = parse_date(m.group(1))

    m = RE_OBJECT.search(text)
    fields.object_name = _clean(m.group(1)) if m else None

    m = RE_CONTRACTOR.search(text)
    fields.contractor_name = _clean(m.group(1)) if m else None

    m = RE_RESPONSIBLE.search(text)
    fields.responsible = _clean(m.group(1)) if m else None

    m = RE_DESCRIPTION.search(text)
    description = _clean(m.group(1)) if m else None
    if description:
        fields.description = description[:1500]
    else:
        fallback = " ".join(text.split())
        fields.description = fallback[:1500] or None

    fields.items = extract_items(text)
    fields.norm_refs = extract_norm_refs(text)
    return fields
