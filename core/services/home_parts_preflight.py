"""Content preflight for the short homepage request.

Three results: pass, warning, or reject. Deterministic phrase lists only.
A doubtful technical description is allowed through. A wrong block is worse
than a missed one. Exact articles skip this check.

«руль» is not mapped to Салон. The seeded category Салон is a general
interior bucket and is not defined as the steering wheel itself. The bare
word is ambiguous (steering wheel vs steering), so the buyer gets a warning
without a suggested category. The explicit phrase «рулевое колесо» is Салон.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.services.home_parts_category_signals import CATEGORY_PHRASES
from core.services.home_parts_query import looks_like_exact_article
from core.services.home_parts_reject_signals import (
    MEANINGLESS_TOKENS,
    NON_PARTS_PHRASES,
)

WARNING_CATEGORY_MISMATCH = 'category_mismatch'
WARNING_AMBIGUOUS_CATEGORY = 'ambiguous_part_category'
WARNING_MULTIPLE_CATEGORIES = 'multiple_part_categories'
REJECT_NOT_PARTS = 'not_parts_request'
REJECT_UNSPECIFIED_PART = 'unspecified_part'
PREFLIGHT_HTTP_STATUS = 422
WARNING_HTTP_STATUS = PREFLIGHT_HTTP_STATUS

NOT_PARTS_MESSAGE = (
    'Эта форма предназначена только для поиска и покупки автозапчастей. '
    'Укажите название или артикул нужной детали.'
)
UNSPECIFIED_PART_MESSAGE = (
    'Укажите, какая именно запчасть нужна — например: “передние колодки”, '
    '“рулевая рейка” или артикул детали.'
)
AMBIGUOUS_CATEGORY_MESSAGE = (
    'Запрос похож на автозапчасть, но выбранная категория может не '
    'соответствовать описанию. Проверьте категорию перед отправкой.'
)
MULTIPLE_CATEGORIES_MESSAGE = (
    'В запросе указаны запчасти из разных категорий. Лучше отправить их '
    'отдельными заявками или проверить выбранную категорию.'
)

# Bare «руль» is intentionally not a category phrase. See module docstring.
_AMBIGUOUS_PART_PHRASES: tuple[str, ...] = ('руль',)

_CONFIRMED_VALUES = frozenset({'1', 'true', 'yes', 'on'})
_PUNCT_RE = re.compile(r'[^\w\s]+', re.UNICODE)

_KIND_WARNING = 'warning'
_KIND_REJECT = 'reject'


@dataclass(frozen=True)
class HomePartsPreflight:
    kind: str
    code: str
    message: str
    suggested_category: str = ''

    @property
    def is_reject(self) -> bool:
        return self.kind == _KIND_REJECT

    def as_response(self) -> dict:
        if self.is_reject:
            return {
                'rejected': True,
                'rejection_code': self.code,
                'rejection_message': self.message,
            }
        body = {
            'warning_required': True,
            'warning_code': self.code,
            'warning_message': self.message,
        }
        if self.suggested_category:
            body['suggested_category'] = self.suggested_category
        return body


def warning_is_confirmed(raw: object) -> bool:
    return str(raw or '').strip().casefold() in _CONFIRMED_VALUES


def evaluate_home_parts_preflight(
    *,
    query: str,
    category: str,
    positions: list[str] | None = None,
) -> HomePartsPreflight | None:
    items = list(positions) if positions is not None else [str(query or '')]
    if items and all(looks_like_exact_article(item) for item in items):
        return None

    text = normalize_preflight_text(query)
    if not text:
        return None

    matched = _matched_categories(text)
    if len(matched) > 1:
        return HomePartsPreflight(
            kind=_KIND_WARNING,
            code=WARNING_MULTIPLE_CATEGORIES,
            message=MULTIPLE_CATEGORIES_MESSAGE,
        )
    if len(matched) == 1:
        suggested = next(iter(matched))
        if suggested.casefold() != normalize_preflight_text(category):
            return HomePartsPreflight(
                kind=_KIND_WARNING,
                code=WARNING_CATEGORY_MISMATCH,
                message=_category_mismatch_message(suggested, category),
                suggested_category=suggested,
            )
        return None
    # A clear but uncategorized part wins over a service phrase.
    if _contains_any(text, _AMBIGUOUS_PART_PHRASES):
        return HomePartsPreflight(
            kind=_KIND_WARNING,
            code=WARNING_AMBIGUOUS_CATEGORY,
            message=AMBIGUOUS_CATEGORY_MESSAGE,
        )
    if _contains_any(text, NON_PARTS_PHRASES):
        return HomePartsPreflight(
            kind=_KIND_REJECT,
            code=REJECT_NOT_PARTS,
            message=NOT_PARTS_MESSAGE,
        )
    if _is_meaningless(text):
        return HomePartsPreflight(
            kind=_KIND_REJECT,
            code=REJECT_UNSPECIFIED_PART,
            message=UNSPECIFIED_PART_MESSAGE,
        )
    return None


def normalize_preflight_text(value: object) -> str:
    text = str(value or '').replace('ё', 'е').replace('Ё', 'Е')
    text = _PUNCT_RE.sub(' ', text)
    return ' '.join(text.casefold().split())


def _category_mismatch_message(suggested: str, selected: str) -> str:
    chosen = ' '.join(str(selected or '').split())
    return (
        f'По тексту запроса похоже, что вам нужна категория “{suggested}”, '
        f'а выбрана “{chosen}”. Проверьте категорию.'
    )


def _matched_categories(text: str) -> set[str]:
    found: list[tuple[str, int, int]] = []
    for category_name, phrases in CATEGORY_PHRASES:
        for phrase in phrases:
            for start, end in _phrase_spans(text, phrase):
                found.append((category_name, start, end))
    kept = _keep_longest_spans(found)
    return {category_name for category_name, _start, _end in kept}


def _keep_longest_spans(
    found: list[tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    ordered = sorted(found, key=lambda item: (item[1] - item[2], item[1]))
    kept: list[tuple[str, int, int]] = []
    for candidate in ordered:
        _category, start, end = candidate
        if any(start >= kept_start and end <= kept_end for _name, kept_start, kept_end in kept):
            continue
        kept.append(candidate)
    return kept


def _is_meaningless(text: str) -> bool:
    tokens = text.split()
    return bool(tokens) and all(token in MEANINGLESS_TOKENS for token in tokens)


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_phrase_spans(text, phrase) for phrase in phrases)


def _phrase_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    pattern = rf'(?<!\w){re.escape(phrase)}(?!\w)'
    return [(match.start(), match.end()) for match in re.finditer(pattern, text)]
