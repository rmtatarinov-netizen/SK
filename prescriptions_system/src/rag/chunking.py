"""Чанкинг нормативных документов по границам пунктов (Шаг 7).

Токены считаются приближённо (по словам). Кусочки одного пункта не
смешиваются с соседними пунктами; пересечение (overlap) переносит хвост
предыдущего чанка в следующий. Номера вида «5.1» не режутся на предложения.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Заголовки пунктов: "5.1 ", "п. 5.3.1", "3.2.1." в начале строки.
_ITEM_HEADING = re.compile(
    r"^\s*(?:п\.|пункт|раздел|глава)?\s*(\d+(?:[.\-]\d+){0,3})[.)]?\s",
    re.MULTILINE,
)
# Граница предложения: пунктуация + пробел (не режет "5.1").
_SENT_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    chunk_index: int
    section: str | None
    item: str | None
    text: str
    start_char: int
    end_char: int


def _approx_tokens(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _split_sentences(block: str) -> list[str]:
    parts = [p for p in _SENT_BOUNDARY.split(block) if p and p.strip()]
    if parts:
        return parts
    stripped = block.strip()
    return [stripped] if stripped else []


def _split_blocks(text: str) -> list[tuple[str | None, str, int, int]]:
    matches = list(_ITEM_HEADING.finditer(text))
    if not matches:
        return [(None, text, 0, len(text))]
    blocks: list[tuple[str | None, str, int, int]] = []
    if matches[0].start() > 0:
        prelude = text[: matches[0].start()]
        if prelude.strip():
            blocks.append((None, prelude, 0, matches[0].start()))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = text[m.start() : end]
        if raw.strip():
            blocks.append((m.group(1), raw, m.start(), end))
    return blocks


def _overlap_sentence_count(sentences: list[str], overlap_tokens: int) -> int:
    total = 0
    count = 0
    for sentence in sentences[:-1][::-1]:
        st = _approx_tokens(sentence)
        if total + st > overlap_tokens:
            break
        total += st
        count += 1
    return count


def chunk_text(
    text: str, *, chunk_size_tokens: int = 500, overlap_tokens: int = 50
) -> list[Chunk]:
    if not text or not text.strip():
        return []

    chunks: list[Chunk] = []
    for item, raw, start, end in _split_blocks(text):
        sentences = _split_sentences(raw)
        i = 0
        while i < len(sentences):
            take: list[str] = [sentences[i]]
            tokens_used = _approx_tokens(sentences[i])
            j = i + 1
            while j < len(sentences):
                st = _approx_tokens(sentences[j])
                if tokens_used + st > chunk_size_tokens and len(take) >= 1:
                    break
                take.append(sentences[j])
                tokens_used += st
                j += 1
            chunks.append(
                Chunk(
                    chunk_index=len(chunks),
                    section=None,
                    item=item,
                    text="".join(take).strip(),
                    start_char=start,
                    end_char=end,
                )
            )
            if j >= len(sentences):
                break
            carry = _overlap_sentence_count(take, overlap_tokens)
            i = max(i + 1, j - carry)  # прогресс гарантирован

    chunks = [c for c in chunks if c.text]
    for index, chunk in enumerate(chunks):
        chunk.chunk_index = index
    return chunks
