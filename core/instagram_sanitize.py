"""
Очистка пользовательского текста заявки перед публикацией в Instagram.
"""

from __future__ import annotations

import re

DEFAULT_MAX_DESCRIPTION_LENGTH = 200
INSTAGRAM_PART_FALLBACK = 'Запчасть по заявке'

JUNK_DESCRIPTION_TOKENS = frozenset({
    'test',
    'тест',
    'qwerty',
    'asdf',
    '123',
    'abc',
    'йцук',
    'фыва',
    'ячсм',
})

_VOWELS = frozenset('aeiouyаеёиоуыэюя')

_PHONE_PATTERN = re.compile(
    r'(?:'
    r'\+?\d[\d\s().\-]{8,}\d'
    r'|'
    r'\b(?:7|8)\d{10}\b'
    r'|'
    r'\b\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b'
    r')',
    flags=re.IGNORECASE,
)

_EMAIL_PATTERN = re.compile(
    r'\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b',
    flags=re.IGNORECASE,
)

_URL_PATTERN = re.compile(
    r'(?:https?://|www\.)\S+|'
    r'\b[a-z0-9][a-z0-9\-]*\.(?:kz|ru|com|net|org|io|me|app|shop|store)\S*',
    flags=re.IGNORECASE,
)

_VIN_PATTERN = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b', flags=re.IGNORECASE)

_FRAGMENT_SPLIT_RE = re.compile(r'[—\-|/,;]+')


def _normalize_spaces(value: str | None) -> str:
    return ' '.join(str(value or '').split())


def _clean_fragment(fragment: str) -> str:
    return re.sub(r'^[^\w]+', '', fragment, flags=re.UNICODE).strip()


def _has_low_word_likeness(text: str) -> bool:
    compact = _clean_fragment(text)
    if len(compact) < 5 or not compact.isalpha():
        return False

    common_markers = (
        'ие', 'ия', 'ная', 'ный', 'ель', 'тор', 'атель', 'система',
        'колод', 'фильтр', 'двиг', 'кузов', 'тормоз', 'подвес',
    )
    lowered = compact.lower()
    if any(marker in lowered for marker in common_markers):
        return False

    unique_ratio = len(set(lowered)) / len(lowered)
    if unique_ratio >= 0.78 and len(lowered) <= 14:
        return True

    return False


def is_junk_text_fragment(value: str | None) -> bool:
    """True, если фрагмент текста выглядит как мусор или случайный набор символов."""
    text = _normalize_spaces(value).lower()
    if not text:
        return True

    core = _clean_fragment(text)
    if not core:
        return True

    if core in JUNK_DESCRIPTION_TOKENS:
        return True

    tokens = [token for token in re.sub(r'[^\w\s]', ' ', core, flags=re.UNICODE).split() if token]
    if tokens and all(token in JUNK_DESCRIPTION_TOKENS for token in tokens):
        return True

    compact = core.replace(' ', '')
    if len(compact) <= 2:
        return True

    if len(compact) >= 3 and len(set(compact)) <= 2:
        return True

    if re.fullmatch(r'(.)\1{2,}', compact):
        return True

    if re.search(r'[bcdfghjklmnpqrstvwxzбвгджзйклмнпрстфхцчшщ]{6,}', core, re.IGNORECASE):
        return True

    letters = [char for char in core if char.isalpha()]
    if len(letters) >= 5:
        vowel_ratio = sum(1 for char in letters if char in _VOWELS) / len(letters)
        if vowel_ratio < 0.12:
            return True

    alnum_count = sum(1 for char in core if char.isalnum())
    if alnum_count / len(core) < 0.45:
        return True

    if _has_low_word_likeness(core):
        return True

    return False


def is_junk_only_description(value: str | None) -> bool:
    """
    True, если description целиком состоит из мусора.

    Пустое описание не считается мусором — публикация может идти по категории заявки.
    """
    text = _normalize_spaces(value)
    if not text:
        return False

    sanitized = sanitize_description(text)
    if not sanitized:
        return True

    for fragment in _split_description_fragments(sanitized):
        if not is_junk_text_fragment(fragment):
            return False

    return True


def sanitize_description(
    value: str | None,
    *,
    max_length: int = DEFAULT_MAX_DESCRIPTION_LENGTH,
) -> str:
    """
    Удаляет телефоны, email, ссылки, VIN-подобные значения и лишние пробелы.
    """
    text = _normalize_spaces(value)
    if not text:
        return ''

    text = _PHONE_PATTERN.sub(' ', text)
    text = _EMAIL_PATTERN.sub(' ', text)
    text = _URL_PATTERN.sub(' ', text)
    text = _VIN_PATTERN.sub(' ', text)
    text = _normalize_spaces(text)

    if len(text) > max_length:
        text = text[: max_length - 1].rstrip() + '…'

    return text


def _split_description_fragments(description: str) -> list[str]:
    fragments: list[str] = []
    for part in _FRAGMENT_SPLIT_RE.split(description):
        cleaned = _normalize_spaces(part)
        if cleaned:
            fragments.append(cleaned)
    return fragments or [_normalize_spaces(description)]


def _description_adds_value(category: str, description: str) -> bool:
    category_lower = category.lower()
    description_lower = description.lower()
    if description_lower == category_lower:
        return False
    if category_lower in description_lower or description_lower in category_lower:
        return False
    return True


def build_instagram_part_text(
    *,
    category: str | None = None,
    description: str | None = None,
    article: str | None = None,
) -> str:
    """
    Формирует безопасный текст детали для Instagram Story.

    Приоритет: категория → нормальное описание → fallback «Запчасть по заявке».
    Мусорные фрагменты описания игнорируются.
    """
    category_text = _normalize_spaces(category)
    sanitized_description = sanitize_description(description)

    useful_parts: list[str] = []
    if sanitized_description:
        for fragment in _split_description_fragments(sanitized_description):
            cleaned_fragment = _clean_fragment(fragment)
            if cleaned_fragment and not is_junk_text_fragment(cleaned_fragment):
                useful_parts.append(cleaned_fragment)

    useful_description = _normalize_spaces(' '.join(useful_parts))

    if category_text and not is_junk_text_fragment(category_text):
        if useful_description and _description_adds_value(category_text, useful_description):
            part_text = f'{category_text} — {useful_description}'
        else:
            part_text = category_text
    elif useful_description:
        part_text = useful_description
    else:
        part_text = INSTAGRAM_PART_FALLBACK

    article_text = _normalize_spaces(article)
    if article_text and not is_junk_text_fragment(article_text):
        part_text = f'{part_text} · Арт. {article_text}'

    return part_text


def build_instagram_geography_text(
    *,
    search_scope: str | None = None,
    city: str | None = None,
    selected_cities: str | None = None,
) -> str:
    """Формирует строку географии для Instagram Story."""
    scope = (search_scope or 'city').strip().lower()

    if scope == 'kazakhstan':
        return 'Весь Казахстан'

    if scope == 'custom':
        cities = [
            item.strip()
            for item in str(selected_cities or '').split(',')
            if item.strip()
        ]
        if len(cities) == 1:
            return cities[0]
        if len(cities) > 1:
            return 'Выбранные города'

    city_text = _normalize_spaces(city)
    if city_text:
        return city_text

    return 'Казахстан'
