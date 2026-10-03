"""Версионированные промпты LLM (Шаг 6).

Каждый промпт имеет версию. Смена текста промпта повышает версию, чтобы
результат в llm_rag_logs оставался воспроизводимым. LLM не подтверждает
нормативные ссылки и не выдумывает значения: при отсутствии — null.
"""

from __future__ import annotations

EXTRACT_FIELDS_VERSION = "extract_fields_v1"
EXTRACT_NORM_REFS_VERSION = "extract_norm_refs_v1"

_JSON_GUARD = "Ответь ТОЛЬКО валидным JSON без пояснений и markdown."


def extract_fields_prompt(ocr_text: str) -> str:
    return f"""Ты извлекаешь структурированные данные из предписания \
строительного контроля.

Вход: OCR-текст документа (возможны ошибки распознавания).

Верни JSON по схеме:
{{
  "doc_number": string | null,
  "doc_date": "YYYY-MM-DD" | null,
  "object_name": string | null,
  "contractor_name": string | null,
  "description": string | null,
  "violation": string | null,
  "deadline": "YYYY-MM-DD" | null,
  "responsible_person": string | null,
  "items": [
    {{"text": string, "deadline": "YYYY-MM-DD" | null, "explicit_norm_refs": [string]}}
  ],
  "confidence": number
}}

Правила:
- Если значение отсутствует в тексте — поставь null. Не выдумывай номера и даты.
- "confidence" — число 0..1: насколько уверенно извлечены поля.
- "description" — нормализованное краткое описание нарушения.
- "items" — нумерованные пункты предписания; для каждого укажи срок и
  ЯВНО перечисленные нормативные ссылки (explicit_norm_refs), если они есть.
- Нормативные ссылки считай указанными, только если они буквально есть в тексте.
- { _JSON_GUARD }

OCR-ТЕКСТ:
\"\"\"{ocr_text}\"\"\""""


def extract_norm_refs_prompt(text: str) -> str:
    return f"""Извлеки из текста предписания ЯВНО указанные нормативные ссылки \
(СНиП, СП, ГОСТ, СТО, ТУ, РД) с пунктами, если они приведены.

Верни JSON по схеме:
{{
  "refs": [
    {{"doc_code": string, "item": string | null, "quote": string}}
  ]
}}

Правила:
- Возвращай только ссылки, буквально присутствующие в тексте (quote — фрагмент).
- Не додумывай несуществующие документы и номера.
- { _JSON_GUARD }

ТЕКСТ:
\"\"\"{text}\"\"\""""
