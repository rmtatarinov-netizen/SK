"""Версионированные промпты LLM (Шаг 6).

Каждый промпт имеет версию. Смена текста промпта повышает версию, чтобы
результат в llm_rag_logs оставался воспроизводимым. LLM не подтверждает
нормативные ссылки и не выдумывает значения: при отсутствии — null.
"""

from __future__ import annotations

EXTRACT_FIELDS_VERSION = "extract_fields_v1"
EXTRACT_NORM_REFS_VERSION = "extract_norm_refs_v1"
RECOMMEND_NORMS_VERSION = "recommend_norms_v1"

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


def recommend_norms_prompt(item_text: str, fragments: list[dict]) -> str:
    """LLM выбирает подходящие пункты норм ТОЛЬКО из переданных RAG-фрагментов.

    LLM не имеет права выдавать ссылку, отсутствующую в загрузке; поэтому он
    выбирает по index фрагмента, а нормативный код берётся из метаданных
    фрагмента, а не из ответа модели.
    """
    enumerated = "\n".join(
        f'[{i}] ({frag.get("doc_code", "?")} '
        f'{"п." + str(frag.get("item")) if frag.get("item") else ""}): '
        f'{frag.get("text", "")}'
        for i, frag in enumerate(fragments)
    )
    return f"""Подбери к пункту предписания релевантные фрагменты нормативных документов.

Используй ТОЛЬКО фрагменты из списка ниже (по их номерам [i]). Не придумывай
свои ссылки, документы или пункты. Если ни один фрагмент не подходит — верни
пустые matches. Выбери не более 3 релевантных.

Пункт предписания:
\"\"\"{item_text}\"\"\"

Фрагменты нормативной базы:
{enumerated}

Верни JSON по схеме:
{{
  "matches": [
    {{"index": integer, "reason": string}}
  ]
}}

- { _JSON_GUARD }"""
