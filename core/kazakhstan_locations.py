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

# First daily seller-growth circle: Almaty, Astana and all 17 oblast centres.
# Shymkent remains in the nationwide registry but is not in this first circle.
FIRST_CIRCLE_CITIES: tuple[str, ...] = (
    'Алматы',
    'Астана',
    'Семей',
    'Кокшетау',
    'Актобе',
    'Конаев',
    'Атырау',
    'Уральск',
    'Тараз',
    'Талдыкорган',
    'Караганда',
    'Костанай',
    'Кызылорда',
    'Актау',
    'Павлодар',
    'Петропавловск',
    'Туркестан',
    'Жезказган',
    'Усть-Каменогорск',
)

FIRST_CIRCLE_SELLER_SATURATION_TARGET = 30

# Explicit aliases and common legacy spellings. This is intentionally bounded:
# an unknown value is never fuzzy-corrected into a different city.
KAZAKHSTAN_CITY_ALIASES = {
    'Almaty': 'Алматы',
    'Alma-Ata': 'Алматы',
    'Алма-Ата': 'Алматы',
    'Аматы': 'Алматы',
    'Astana': 'Астана',
    'Nur-Sultan': 'Астана',
    'Nursultan': 'Астана',
    'Нур-Султан': 'Астана',
    'Нурсултан': 'Астана',
    'Shymkent': 'Шымкент',
    'Chimkent': 'Шымкент',
    'Aktobe': 'Актобе',
    'Aqtobe': 'Актобе',
    'Karaganda': 'Караганда',
    'Karagandy': 'Караганда',
    'Qaragandy': 'Караганда',
    'Taraz': 'Тараз',
    'Atyrau': 'Атырау',
    'Oskemen': 'Усть-Каменогорск',
    'Öskemen': 'Усть-Каменогорск',
    'Өскемен': 'Усть-Каменогорск',
    'Усть Каменогорск': 'Усть-Каменогорск',
    'Ust-Kamenogorsk': 'Усть-Каменогорск',
    'Pavlodar': 'Павлодар',
    'Semey': 'Семей',
    'Semipalatinsk': 'Семей',
    'Kyzylorda': 'Кызылорда',
    'Qyzylorda': 'Кызылорда',
    'Kostanay': 'Костанай',
    'Kostanai': 'Костанай',
    'Qostanai': 'Костанай',
    'Aktau': 'Актау',
    'Aqtau': 'Актау',
    'Oral': 'Уральск',
    'Uralsk': 'Уральск',
    'Petropavl': 'Петропавловск',
    'Petropavlovsk': 'Петропавловск',
    'Turkistan': 'Туркестан',
    'Turkestan': 'Туркестан',
    'Kokshetau': 'Кокшетау',
    'Taldykorgan': 'Талдыкорган',
    'Taldyqorgan': 'Талдыкорган',
    'Zhezkazgan': 'Жезказган',
    'Jezkazgan': 'Жезказган',
    'Konaev': 'Конаев',
    'Qonaev': 'Конаев',
    'Qonayev': 'Конаев',
    'Konayev': 'Конаев',
    'Капчагай': 'Конаев',
    'Kapchagay': 'Конаев',
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


def canonicalize_kazakhstan_cities(values) -> list[str]:
    """Return unique canonical Kazakhstan cities, preserving input order.

    Unknown and empty values are ignored. Callers that require strict
    validation should compare the result length with their non-empty inputs.
    """
    result: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        canonical = canonical_kazakhstan_city(value)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    return result


def canonical_kazakhstan_city_or_original(value: object) -> str:
    """Canonicalize a known Kazakhstan city and preserve a non-KZ value.

    This is intended for discovery/import provenance where foreign locations
    may legitimately exist. Public Kazakhstan forms should use
    canonical_kazakhstan_city() and reject unknown values instead.
    """
    text = ' '.join(str(value or '').split())
    if not text:
        return ''
    return canonical_kazakhstan_city(text) or text
