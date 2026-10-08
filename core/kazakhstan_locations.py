"""Canonical Kazakhstan city names for public forms, checkout, and seller discovery.\n\nThe registry contains all 90 cities reported by Kazakhstan official statistics\nas of 2026-07-01. Major cities stay first so daily seller discovery reaches\nthe largest markets early while still rotating through the full country.\n"""

from __future__ import annotations

KAZAKHSTAN_CITIES = [
    'Алматы',
    'Астана',
    'Шымкент',
    'Актобе',
    'Караганда',
    'Тараз',
    'Атырау',
    'Усть-Каменогорск',
    'Павлодар',
    'Семей',
    'Кызылорда',
    'Костанай',
    'Актау',
    'Уральск',
    'Петропавловск',
    'Туркестан',
    'Кокшетау',
    'Темиртау',
    'Талдыкорган',
    'Экибастуз',
    'Рудный',
    'Абай',
    'Акколь',
    'Аксай',
    'Аксу',
    'Алатау',
    'Алга',
    'Алтай',
    'Арал',
    'Аркалык',
    'Арыс',
    'Атбасар',
    'Аягоз',
    'Байконыр',
    'Балхаш',
    'Булаево',
    'Державинск',
    'Ерейментау',
    'Есик',
    'Есиль',
    'Жанаозен',
    'Жанатас',
    'Жаркент',
    'Жезказган',
    'Жем',
    'Жетысай',
    'Житикара',
    'Зайсан',
    'Казалинск',
    'Кандыагаш',
    'Каражал',
    'Каратау',
    'Каркаралинск',
    'Каскелен',
    'Кентау',
    'Конаев',
    'Косшы',
    'Кулсары',
    'Курчатов',
    'Ленгер',
    'Лисаковск',
    'Макинск',
    'Мамлютка',
    'Приозёрск',
    'Риддер',
    'Сарань',
    'Сарканд',
    'Сарыагаш',
    'Сатпаев',
    'Сергеевка',
    'Серебрянск',
    'Степногорск',
    'Степняк',
    'Тайынша',
    'Талгар',
    'Текели',
    'Темир',
    'Тобыл',
    'Ушарал',
    'Уштобе',
    'Форт-Шевченко',
    'Хромтау',
    'Шалкар',
    'Шар',
    'Шардара',
    'Шахтинск',
    'Шемонаиха',
    'Шу',
    'Щучинск',
    'Эмба',
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
