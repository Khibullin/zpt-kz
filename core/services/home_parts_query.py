"""Parse short-form part queries without treating every comma as a new part."""
from __future__ import annotations

import re

QUERY_MAX_LENGTH = 500
QUERY_MIN_LENGTH = 2
MAX_PART_POSITIONS = 8

_CYRILLIC_RE = re.compile(r'[А-Яа-яЁё]')
_LATIN_WORD_RE = re.compile(r'[A-Za-z]{4,}')
_DIGIT_RE = re.compile(r'\d')
_CODE_TOKEN_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9./_-]*$')
_DECIMAL_COMMA_RE = re.compile(r'(?<=\d),(?=\d)')
_SPLIT_CHUNK_RE = re.compile(r'[\n;]+')
_PROTECTED_COMMA = '\u0000'


def split_part_queries(raw: str) -> list[str]:
    """Split several parts, keeping decimal commas like 1,6 or 2,0 intact."""
    text = str(raw or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        return []

    protected = _DECIMAL_COMMA_RE.sub(_PROTECTED_COMMA, text)
    items: list[str] = []
    for chunk in _SPLIT_CHUNK_RE.split(protected):
        for piece in chunk.split(','):
            item = piece.replace(_PROTECTED_COMMA, ',').strip()
            if item:
                items.append(item)
    return items or [text]


def looks_like_exact_article(query: str) -> bool:
    """True only for compact OEM/SKU codes, not names like «фильтр H75»."""
    text = ' '.join(str(query or '').split())
    if not text:
        return False
    if _CYRILLIC_RE.search(text):
        return False

    words = text.split()
    named_words = [
        word for word in words
        if _LATIN_WORD_RE.search(word) and not _DIGIT_RE.search(word)
    ]
    if named_words:
        return False

    compact = re.sub(r'[\s\-]', '', text)
    if len(compact) < 5 or not _DIGIT_RE.search(compact):
        return False
    if len(words) > 2:
        return False
    return all(_CODE_TOKEN_RE.match(word) and len(word) <= 24 for word in words)


def query_requires_vehicle(query: str) -> bool:
    return not looks_like_exact_article(query)
