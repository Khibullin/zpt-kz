"""
Очистка пользовательского текста заявки перед публикацией в Instagram.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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

_UPPERCASE_WORDS = {
    'акп': 'АКПП',
    'акпп': 'АКПП',
    'мкп': 'МКПП',
    'мкпп': 'МКПП',
}

_PREFIX_WORDS_RE = re.compile(
    r'^(?:'
    r'нужен срочно|нужна срочно|'
    r'нужен|нужна|нужно|нужны|'
    r'ищу|надо|требуется|куплю'
    r')\s+',
    flags=re.IGNORECASE,
)

_PHRASE_TYPOS: tuple[tuple[str, str], ...] = (
    ('ищу фару перднию', 'передняя фара'),
    ('фару перднию', 'передняя фара'),
    ('фара перднию', 'передняя фара'),
    ('коробка автомат', 'акпп'),
)

_WORD_TYPOS: dict[str, str] = {
    'бампр': 'бампер',
    'бамперр': 'бампер',
    'передни': 'передний',
    'перадний': 'передний',
    'пердний': 'передний',
    'переднею': 'переднюю',
    'перднию': 'переднюю',
    'двегатель': 'двигатель',
    'двигател': 'двигатель',
    'движок': 'двигатель',
    'каробка': 'коробка',
    'капотт': 'капот',
    'крылоо': 'крыло',
    'радиаторр': 'радиатор',
}

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


@dataclass(frozen=True)
class InstagramPartDisplay:
    detail: str
    category_line: str = ''


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
        'колод', 'фильтр', 'двиг', 'кузов', 'тормоз', 'подвес', 'бампер', 'фара',
        'оптик',
    )
    lowered = compact.lower()
    if any(marker in lowered for marker in common_markers):
        return False

    unique_ratio = len(set(lowered)) / len(lowered)
    if unique_ratio >= 0.78 and len(lowered) <= 14:
        return True

    return False


def is_garbage_text(value: str | None) -> bool:
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


is_junk_text_fragment = is_garbage_text


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
        if not is_garbage_text(fragment):
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


def fix_common_part_typos(value: str) -> str:
    """Исправляет частые опечатки в описании детали."""
    text = _normalize_spaces(value).lower()
    if not text:
        return ''

    for source, replacement in sorted(_PHRASE_TYPOS, key=lambda item: -len(item[0])):
        text = text.replace(source, replacement)

    words = text.split()
    fixed_words = [_WORD_TYPOS.get(word, word) for word in words]
    return _normalize_spaces(' '.join(fixed_words))


def _strip_leading_service_words(value: str) -> str:
    text = _normalize_spaces(value)
    while text:
        updated = _PREFIX_WORDS_RE.sub('', text, count=1).strip()
        if updated == text:
            break
        text = updated
    return text


def normalize_instagram_part_text(value: str | None) -> str:
    """Нормализует публичный текст детали: опечатки, служебные слова, капитализация."""
    text = fix_common_part_typos(_strip_leading_service_words(_normalize_spaces(value)))
    if not text:
        return ''

    words = text.split()
    normalized_words: list[str] = []
    for index, word in enumerate(words):
        lowered = word.lower()
        if lowered in _UPPERCASE_WORDS:
            normalized_words.append(_UPPERCASE_WORDS[lowered])
            continue
        if word.isupper() and len(word) <= 5:
            normalized_words.append(word)
            continue
        if index == 0:
            normalized_words.append(word[:1].upper() + word[1:].lower() if word else word)
        else:
            normalized_words.append(word.lower())

    return _normalize_spaces(' '.join(normalized_words))


def clean_public_part_description(value: str | None) -> str:
    """Очищает описание детали для публичного Instagram-баннера."""
    sanitized = sanitize_description(value)
    if not sanitized:
        return ''

    useful_parts: list[str] = []
    for fragment in _split_description_fragments(sanitized):
        cleaned_fragment = _clean_fragment(fragment)
        if cleaned_fragment and not is_garbage_text(cleaned_fragment):
            useful_parts.append(cleaned_fragment)

    return _normalize_spaces(' '.join(useful_parts))


def build_instagram_part_display(
    *,
    category: str | None = None,
    description: str | None = None,
    article: str | None = None,
) -> InstagramPartDisplay:
    """
    Формирует публичный текст детали и строку категории для Instagram Story.
    """
    category_text = _normalize_spaces(category)

    cleaned_description = clean_public_part_description(description)
    normalized_description = ''
    if cleaned_description:
        normalized_description = normalize_instagram_part_text(cleaned_description)

    if (
        category_text
        and normalized_description
        and normalized_description.lower() == category_text.lower()
    ):
        normalized_description = ''

    detail = ''
    if normalized_description:
        detail = normalized_description
    elif category_text:
        detail = normalize_instagram_part_text(category_text)
    else:
        detail = INSTAGRAM_PART_FALLBACK

    category_line = ''
    if (
        category_text
        and detail.lower() != category_text.lower()
        and detail != INSTAGRAM_PART_FALLBACK
    ):
        category_line = f'Категория: {normalize_instagram_part_text(category_text)}'

    article_text = _normalize_spaces(article)
    if article_text and not is_garbage_text(article_text):
        detail = f'{detail} · Арт. {article_text}'

    return InstagramPartDisplay(detail=detail, category_line=category_line)


def build_instagram_part_text(
    *,
    category: str | None = None,
    description: str | None = None,
    article: str | None = None,
) -> str:
    """Краткий текст детали для caption и обратной совместимости."""
    display = build_instagram_part_display(
        category=category,
        description=description,
        article=article,
    )
    if display.category_line:
        return f'{display.detail} ({display.category_line})'
    return display.detail


def build_instagram_buyer_city_text(*, city: str | None = None) -> str:
    """Город покупателя для Instagram Story."""
    city_text = _normalize_spaces(city)
    return city_text or 'Казахстан'


def build_instagram_seller_search_text(
    *,
    search_scope: str | None = None,
    city: str | None = None,
    selected_cities: str | None = None,
) -> str:
    """Где ищем продавцов для Instagram Story."""
    scope = (search_scope or 'city').strip().lower()

    if scope == 'kazakhstan':
        return 'весь Казахстан'

    if scope == 'city':
        return 'только город покупателя'

    if scope == 'custom':
        cities = [
            item.strip()
            for item in str(selected_cities or '').split(',')
            if item.strip()
        ]
        if len(cities) == 1:
            return cities[0]
        if len(cities) > 1:
            return 'выбранные города'

    city_text = _normalize_spaces(city)
    if city_text:
        return city_text

    return 'Казахстан'


def build_instagram_geography_text(
    *,
    search_scope: str | None = None,
    city: str | None = None,
    selected_cities: str | None = None,
) -> str:
    """Обратная совместимость: краткая география в одной строке."""
    buyer_city = build_instagram_buyer_city_text(city=city)
    seller_search = build_instagram_seller_search_text(
        search_scope=search_scope,
        city=city,
        selected_cities=selected_cities,
    )
    return f'{buyer_city} / {seller_search}'
