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
    'Жезказган',
    'Экибастуз',
]

# Exact Latin spellings seen in production. Not a fuzzy dictionary.
KAZAKHSTAN_CITY_ALIASES = {
    'Almaty': 'Алматы',
    'Astana': 'Астана',
}

_CITY_BY_CASEFOLD = {name.casefold(): name for name in KAZAKHSTAN_CITIES}
_CITY_BY_CASEFOLD.update(
    {alias.casefold(): canonical for alias, canonical in KAZAKHSTAN_CITY_ALIASES.items()}
)


def canonical_kazakhstan_city(value: object) -> str | None:
    """Return canonical city spelling after trim, or None if unknown.

    Matching is exact and case-insensitive against canonical names and
    explicit aliases. Unknown values are not fuzzy-corrected.
    """
    text = ' '.join(str(value or '').split())
    if not text:
        return None
    return _CITY_BY_CASEFOLD.get(text.casefold())
