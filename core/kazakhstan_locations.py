"""Canonical Kazakhstan city names for public forms and checkout."""

from __future__ import annotations

KAZAKHSTAN_CITIES = [
    'Алматы',
    'Астана',
    'Шымкент',
    'Караганда',
    'Актобе',
    'Тараз',
    'Павлодар',
    'Усть-Каменогорск',
    'Семей',
    'Атырау',
    'Костанай',
    'Кызылорда',
    'Уральск',
    'Петропавловск',
    'Актау',
    'Темиртау',
    'Туркестан',
    'Кокшетау',
    'Талдыкорган',
    'Экибастуз',
]

_CITY_BY_CASEFOLD = {name.casefold(): name for name in KAZAKHSTAN_CITIES}


def canonical_kazakhstan_city(value: object) -> str | None:
    """Return canonical city spelling after trim, or None if unknown.

    Matching is exact and case-insensitive. Unknown values are not fuzzy-corrected.
    """
    text = ' '.join(str(value or '').split())
    if not text:
        return None
    return _CITY_BY_CASEFOLD.get(text.casefold())
