"""High-confidence preflight warnings for the short homepage request.

Deterministic phrase lists only. A doubtful query is allowed through:
a wrong warning is worse than a missed one. Exact articles are not scanned.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.services.home_parts_query import looks_like_exact_article

WARNING_CATEGORY_MISMATCH = 'category_mismatch'
WARNING_NON_PARTS = 'non_parts_intent'
WARNING_HTTP_STATUS = 422

NON_PARTS_MESSAGE = (
    'Похоже, это не запрос на покупку автозапчасти. '
    'Проверьте текст перед отправкой.'
)

# Longer phrases are listed first so a specific form is obvious in review.
# Matching itself does not depend on order.
_CATEGORY_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('Тормоза', (
        'тормозные колодки',
        'тормозных колодок',
        'тормозной диск',
        'колодки',
        'суппорт',
    )),
    ('Трансмиссия', (
        'коробка передач',
        'привод',
        'граната',
        'шрус',
        'кпп',
        'сцепление',
    )),
    ('Рулевое управление', (
        'рулевая рейка',
        'рулевой наконечник',
        'рулевая тяга',
    )),
    ('Охлаждение', (
        'радиатор',
        'помпа',
        'термостат',
    )),
    ('Оптика', (
        'фара',
        'фонарь',
    )),
    ('Кузов', (
        'бампер',
        'крыло',
        'капот',
        'дверь',
    )),
    ('Топливная система', (
        'топливный насос',
        'топливный фильтр',
        'бензонасос',
        'форсунка',
    )),
    ('Ходовая часть', (
        'подшипник ступицы',
        'амортизатор',
        'ступица',
        'рычаг',
    )),
    ('Электрика', (
        'генератор',
        'стартер',
    )),
)

_NON_PARTS_PHRASES: tuple[str, ...] = (
    'ремонт автомобиля',
    'записаться на сто',
    'шиномонтаж',
    'покраска автомобиля',
    'диагностика автомобиля',
    'продам автомобиль',
    'куплю автомобиль целиком',
    'рекламное предложение',
    'страхование',
    'реклама',
    'кредит',
    'мойка',
    'такси',
)

_CONFIRMED_VALUES = frozenset({'1', 'true', 'yes', 'on'})


@dataclass(frozen=True)
class HomePartsWarning:
    code: str
    message: str
    suggested_category: str = ''

    def as_response(self) -> dict:
        body = {
            'warning_required': True,
            'warning_code': self.code,
            'warning_message': self.message,
        }
        if self.code == WARNING_CATEGORY_MISMATCH and self.suggested_category:
            body['suggested_category'] = self.suggested_category
        return body


def warning_is_confirmed(raw: object) -> bool:
    return str(raw or '').strip().casefold() in _CONFIRMED_VALUES


def evaluate_home_parts_warning(
    *,
    query: str,
    category: str,
    positions: list[str] | None = None,
) -> HomePartsWarning | None:
    items = list(positions) if positions is not None else [str(query or '')]
    if items and all(looks_like_exact_article(item) for item in items):
        return None

    text = _normalize(query)
    if not text:
        return None

    matched = _matched_categories(text)
    # A clear part wins over a service/non-parts phrase in the same text.
    if len(matched) == 1:
        suggested = next(iter(matched))
        if suggested.casefold() != _normalize(category):
            return HomePartsWarning(
                code=WARNING_CATEGORY_MISMATCH,
                message=_category_mismatch_message(suggested, category),
                suggested_category=suggested,
            )
        return None
    if len(matched) > 1:
        return None
    if _contains_any(text, _NON_PARTS_PHRASES):
        return HomePartsWarning(
            code=WARNING_NON_PARTS,
            message=NON_PARTS_MESSAGE,
        )
    return None


def _category_mismatch_message(suggested: str, selected: str) -> str:
    chosen = ' '.join(str(selected or '').split())
    return (
        f'По тексту запроса похоже, что вам нужна категория “{suggested}”, '
        f'а выбрана “{chosen}”. Проверьте категорию.'
    )


def _matched_categories(text: str) -> set[str]:
    found: set[str] = set()
    for category_name, phrases in _CATEGORY_PHRASES:
        if _contains_any(text, phrases):
            found.add(category_name)
    return found


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_contains_phrase(text, phrase) for phrase in phrases)


def _contains_phrase(text: str, phrase: str) -> bool:
    pattern = rf'(?<!\w){re.escape(phrase)}(?!\w)'
    return re.search(pattern, text) is not None


def _normalize(value: object) -> str:
    text = str(value or '').replace('ё', 'е').replace('Ё', 'Е')
    return ' '.join(text.casefold().split())
