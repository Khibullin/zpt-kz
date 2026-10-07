"""Публичное представление заявки для Instagram Banner V2.

Исходный текст заявки не изменяется. Слой только читает поля и собирает
отдельное нормализованное представление.

Принцип: исправлять язык, не менять факты.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.instagram_sanitize import sanitize_description

REASON_AMBIGUOUS_VEHICLE = 'ambiguous_vehicle'
REASON_AMBIGUOUS_GENERATION = 'ambiguous_generation'
REASON_AMBIGUOUS_PART = 'ambiguous_part'
REASON_AMBIGUOUS_SIDE = 'ambiguous_side'
REASON_UNSAFE_PUBLIC_TEXT = 'unsafe_public_text'
REASON_PII_DETECTED = 'pii_detected'
REASON_OTHER = 'other'

REVIEW_REASON_CODES = (
    REASON_AMBIGUOUS_VEHICLE,
    REASON_AMBIGUOUS_GENERATION,
    REASON_AMBIGUOUS_PART,
    REASON_AMBIGUOUS_SIDE,
    REASON_UNSAFE_PUBLIC_TEXT,
    REASON_PII_DETECTED,
    REASON_OTHER,
)

REVIEW_REASON_LABELS = {
    REASON_AMBIGUOUS_VEHICLE: 'Неясно, какой автомобиль указан.',
    REASON_AMBIGUOUS_GENERATION: 'Поколение автомобиля нельзя определить уверенно.',
    REASON_AMBIGUOUS_PART: 'Неясно, какая запчасть нужна.',
    REASON_AMBIGUOUS_SIDE: 'Неясно, какая сторона нужна.',
    REASON_UNSAFE_PUBLIC_TEXT: 'Публичный текст нельзя показать безопасно.',
    REASON_PII_DETECTED: 'В публичном тексте найдены персональные данные.',
    REASON_OTHER: 'Нужна ручная проверка.',
}

_REASON_PRIORITY = (
    REASON_PII_DETECTED,
    REASON_UNSAFE_PUBLIC_TEXT,
    REASON_AMBIGUOUS_VEHICLE,
    REASON_AMBIGUOUS_GENERATION,
    REASON_AMBIGUOUS_PART,
    REASON_AMBIGUOUS_SIDE,
    REASON_OTHER,
)

# Только чтобы убрать из текста детали уже известные марку и модель.
# Не используется, чтобы дописать марку, которой нет в полях заявки.
_ALIAS_GROUPS = (
    ('haval', 'хавал', 'хавейл', 'хавэйл'),
    ('dargo', 'дарго'),
    ('toyota', 'тойота', 'тойоту'),
    ('camry', 'камри'),
    ('hyundai', 'хендай', 'хёндай', 'хундай'),
    ('kia', 'киа'),
    ('honda', 'хонда'),
    ('cr-v', 'crv', 'срв'),
    ('lexus', 'лексус'),
)

# (лемма в тексте, подпись, род, позиция по умолчанию).
# Позиция по умолчанию есть только у фары: утверждённый пример
# «фара правая и левая» публикуется как передние фары, если задняя не названа.
_PART_LEMMAS = (
    ('фары', 'Фара', 'f', 'front'),
    ('фару', 'Фара', 'f', 'front'),
    ('фара', 'Фара', 'f', 'front'),
    ('фар', 'Фара', 'f', 'front'),
    ('колодки', 'Колодки', 'p', ''),
    ('колодок', 'Колодки', 'p', ''),
    ('колодку', 'Колодки', 'p', ''),
    ('колодка', 'Колодки', 'p', ''),
    ('бампера', 'Бампер', 'm', ''),
    ('бампер', 'Бампер', 'm', ''),
    ('капот', 'Капот', 'm', ''),
    ('крыло', 'Крыло', 'n', ''),
    ('дверь', 'Дверь', 'f', ''),
    ('амортизатор', 'Амортизатор', 'm', ''),
    ('рычаг', 'Рычаг', 'm', ''),
    ('фонарь', 'Фонарь', 'm', ''),
    ('стекло', 'Стекло', 'n', ''),
    ('зеркало', 'Зеркало', 'n', ''),
    ('радиатор', 'Радиатор', 'm', ''),
    ('решетка', 'Решетка', 'f', ''),
    ('решётка', 'Решетка', 'f', ''),
    ('подкрылок', 'Подкрылок', 'm', ''),
    ('диски', 'Диски', 'p', ''),
    ('диск', 'Диск', 'm', ''),
    ('суппорт', 'Суппорт', 'm', ''),
    ('генератор', 'Генератор', 'm', ''),
    ('стартер', 'Стартер', 'm', ''),
    ('бензонасос', 'Бензонасос', 'm', ''),
    ('фильтр', 'Фильтр', 'm', ''),
    ('глушитель', 'Глушитель', 'm', ''),
    ('порог', 'Порог', 'm', ''),
)

_BROAD_CATEGORIES = frozenset({
    '',
    'запчасть',
    'запчасти',
    'запчасть по заявке',
    'тормоза',
    'двигатель',
    'кузов',
    'электрика',
    'прочее',
    'другое',
    'ходовая',
    'салон',
})

_NOTE_PATTERNS = (
    (re.compile(r'желательно\s+оригинал', re.IGNORECASE), 'Желательно оригинал'),
    (re.compile(r'\bоригинал\b', re.IGNORECASE), 'Оригинал'),
    (re.compile(r'\bконтракт\w*\b', re.IGNORECASE), 'Контрактная'),
    (re.compile(r'\bб\s*/?\s*у\b', re.IGNORECASE), 'Б/у'),
)

_JUNK_WORDS = frozenset({
    'нужен', 'нужна', 'нужно', 'нужны', 'ищу', 'надо', 'требуется', 'куплю',
    'срочно', 'звоните', 'позвоните', 'пишите', 'напишите', 'пожалуйста',
    'на', 'для', 'от', 'по', 'и', 'или', 'а', 'же', 'бы', 'это', 'эта', 'этот',
    'желательно', 'хочу', 'можно', 'очень',
})

_PII_RE = re.compile(
    r'(?:'
    r'\+?\d[\d\s().\-]{8,}\d'
    r'|@'
    r'|https?://'
    r'|\bwww\.'
    r'|\b[A-HJ-NPR-Z0-9]{17}\b'
    r')',
    re.IGNORECASE,
)

_BOTH_SIDES_RE = re.compile(
    r'(?:прав\w+|лев\w+)\s+и\s+(?:прав\w+|лев\w+)',
    re.IGNORECASE,
)
_OR_SIDES_RE = re.compile(
    r'(?:прав\w+|лев\w+)\s+или\s+(?:прав\w+|лев\w+)',
    re.IGNORECASE,
)


@dataclass
class PublicPartRequest:
    brand: str = ''
    model: str = ''
    year: str = ''
    year_range: str = ''
    generation: str = ''
    parts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    city: str = ''
    public_request_number: str = ''
    review_reason: str = ''
    review_detail: str = ''

    def as_dict(self) -> dict:
        return {
            'brand': self.brand,
            'model': self.model,
            'year': self.year,
            'year_range': self.year_range,
            'generation': self.generation,
            'parts': list(self.parts),
            'notes': list(self.notes),
            'city': self.city,
            'public_request_number': self.public_request_number,
            'review_reason': self.review_reason,
            'review_detail': self.review_detail,
        }


def review_reason_label(code: str) -> str:
    return REVIEW_REASON_LABELS.get(code, REVIEW_REASON_LABELS[REASON_OTHER])


def build_public_part_request(product_request) -> PublicPartRequest:
    """Собрать публичные поля. Не записывает description и другие raw-поля."""
    brand = _clean(getattr(product_request, 'brand', ''))
    model = _clean(getattr(product_request, 'model', ''))
    year_value = getattr(product_request, 'year', None)
    year = str(year_value) if year_value else ''
    city = _clean(getattr(product_request, 'city', ''))
    number = str(product_request.pk) if getattr(product_request, 'pk', None) else ''
    raw_description = getattr(product_request, 'description', '') or ''
    category = _clean(getattr(product_request, 'category', ''))

    reasons: list[str] = []
    if not brand or not model:
        reasons.append(REASON_AMBIGUOUS_VEHICLE)

    contradiction = _vehicle_contradiction(raw_description, brand, model)
    if contradiction:
        reasons.append(REASON_AMBIGUOUS_VEHICLE)

    working = _strip_known_vehicle_words(sanitize_description(raw_description), brand, model)
    working = _strip_secret_tokens(working, product_request)
    notes = _extract_notes(working)
    for pattern, _label in _NOTE_PATTERNS:
        working = pattern.sub(' ', working)
    working = _clean(working)

    side_reason, parts = _extract_parts(working, category)
    if side_reason:
        reasons.append(side_reason)
    if not parts and REASON_AMBIGUOUS_PART not in reasons and REASON_AMBIGUOUS_SIDE not in reasons:
        reasons.append(REASON_AMBIGUOUS_PART)

    public = PublicPartRequest(
        brand=brand,
        model=model,
        year=year,
        year_range='',
        generation='',
        parts=parts,
        notes=notes,
        city=city,
        public_request_number=number,
    )
    if _public_contains_pii(public, product_request):
        reasons.append(REASON_PII_DETECTED)
        public.parts = [part for part in public.parts if not _PII_RE.search(part)]
        public.notes = [note for note in public.notes if not _PII_RE.search(note)]
        public.city = '' if _PII_RE.search(public.city) else public.city

    reason = _pick_reason(reasons)
    public.review_reason = reason
    public.review_detail = review_reason_label(reason) if reason else ''
    return public


def build_banner_v2_caption(public: PublicPartRequest) -> str:
    """Подпись ленты из нормализованных полей. Raw description сюда не копируется."""
    lines = ['🔴 Покупатель ищет запчасти']
    vehicle_bits = [bit for bit in (public.brand, public.model) if bit]
    vehicle = ' '.join(vehicle_bits)
    if public.year and vehicle:
        vehicle = f'{vehicle}, {public.year}'
    elif public.year:
        vehicle = public.year
    if public.year_range and not public.year:
        vehicle = f'{vehicle}, {public.year_range}'.strip(', ')
    if vehicle:
        lines.append(vehicle)
    if public.city:
        lines.append(f'📍 {public.city}')
    if public.parts:
        lines.append('Требуются:')
        for index, part in enumerate(public.parts):
            ending = ';' if index < len(public.parts) - 1 else '.'
            lines.append(f'• {_lower_first(part)}{ending}')
    for note in public.notes:
        lines.append(note)
    lines.extend([
        'Продаёте автозапчасти?',
        'Получайте заявки покупателей на ZPT.KZ.',
        'Ищете запчасть?',
        'Оставьте заявку на ZPT.KZ — ссылка в профиле.',
    ])
    lines.extend(_hashtags(public))
    return '\n'.join(lines)


def _hashtags(public: PublicPartRequest) -> list[str]:
    tags = ['#zptkz', '#автозапчасти', '#автозапчастиказахстан']
    brand_tag = _hashtag_token(public.brand)
    model_tag = _hashtag_token(f'{public.brand}{public.model}')
    if brand_tag:
        tags.append(f'#{brand_tag}')
    if model_tag and model_tag != brand_tag:
        tags.append(f'#{model_tag}')
    return tags[:5]


def _hashtag_token(value: str) -> str:
    cleaned = re.sub(r'[^\w]+', '', value or '', flags=re.UNICODE)
    if not cleaned:
        return ''
    return cleaned[0].upper() + cleaned[1:]


def _lower_first(value: str) -> str:
    if not value:
        return value
    return value[0].lower() + value[1:]


def _clean(value) -> str:
    return ' '.join(str(value or '').split())


def _pick_reason(reasons: list[str]) -> str:
    present = set(reasons)
    for code in _REASON_PRIORITY:
        if code in present:
            return code
    return ''


def _alias_group_for(value: str) -> tuple[str, ...] | None:
    token = _clean(value).casefold().replace(' ', '')
    if not token:
        return None
    for group in _ALIAS_GROUPS:
        if token in group:
            return group
    return None


def _vehicle_contradiction(description: str, brand: str, model: str) -> bool:
    """Другая известная марка в тексте при уже заполненном поле — не угадываем."""
    if not brand and not model:
        return False
    haystack = _clean(description).casefold()
    own = set()
    for value in (brand, model):
        group = _alias_group_for(value)
        if group:
            own.update(group)
        elif value:
            own.add(value.casefold())
    for group in _ALIAS_GROUPS:
        if own.intersection(group):
            continue
        if any(re.search(rf'(?<!\w){re.escape(alias)}(?!\w)', haystack) for alias in group):
            return True
    return False


def _strip_known_vehicle_words(text: str, brand: str, model: str) -> str:
    removable: set[str] = set()
    for value in (brand, model):
        group = _alias_group_for(value)
        if group:
            removable.update(group)
        elif value:
            removable.add(value.casefold())
    if not removable:
        return text
    words = []
    for word in text.split():
        if word.casefold().strip('.,;') not in removable:
            words.append(word)
    return ' '.join(words)


def _strip_secret_tokens(text: str, product_request) -> str:
    for attr in ('access_token', 'short_token'):
        token = str(getattr(product_request, attr, '') or '').strip()
        if token and token in text:
            text = text.replace(token, ' ')
    return _clean(text)


def _extract_notes(text: str) -> list[str]:
    notes: list[str] = []
    remaining = text
    for pattern, label in _NOTE_PATTERNS:
        if pattern.search(remaining) and label not in notes:
            notes.append(label)
            remaining = pattern.sub(' ', remaining)
    return notes


def _extract_parts(text: str, category: str) -> tuple[str, list[str]]:
    normalized = _clean(text)
    if _OR_SIDES_RE.search(normalized):
        return REASON_AMBIGUOUS_SIDE, []

    both = _BOTH_SIDES_RE.search(normalized)
    if both:
        remainder = _BOTH_SIDES_RE.sub(' ', normalized)
        parsed = _parse_segment(remainder, forced_sides=('right', 'left'))
        if parsed is None:
            category_parts = _parts_from_category(category, sides=('right', 'left'), position='')
            if category_parts:
                return '', category_parts
            return REASON_AMBIGUOUS_PART, []
        return '', parsed

    segments = [segment for segment in re.split(r'\s+и\s+|,', normalized) if _clean(segment)]
    if not segments and normalized:
        segments = [normalized]
    parts: list[str] = []
    saw_side_without_noun = False
    for segment in segments or ['']:
        if not _clean(segment) and not category:
            continue
        parsed = _parse_segment(segment)
        if parsed is None:
            if _has_side(segment) or _has_position(segment):
                saw_side_without_noun = True
            continue
        for part in parsed:
            if part not in parts:
                parts.append(part)

    if parts:
        return '', parts

    category_parts = _parts_from_category(category, sides=(), position='')
    if category_parts and not saw_side_without_noun:
        return '', category_parts
    if saw_side_without_noun:
        return REASON_AMBIGUOUS_PART, []
    if _clean(category) and category.casefold() not in _BROAD_CATEGORIES:
        return '', [_title_phrase(category)]
    return '', []


def _parts_from_category(category: str, *, sides: tuple[str, ...], position: str) -> list[str]:
    label = _clean(category)
    if not label or label.casefold() in _BROAD_CATEGORIES:
        return []
    found = _find_lemma(label.casefold())
    if not found:
        return [_title_phrase(label)] if not sides else []
    noun, gender, default_position = found
    used_position = position or default_position
    if not sides:
        return [_compose_part('', used_position, noun, gender)]
    return [_compose_part(side, used_position, noun, gender) for side in sides]


def _parse_segment(segment: str, forced_sides: tuple[str, ...] = ()) -> list[str] | None:
    tokens = _clean(segment).casefold().split()
    tokens = [token.strip('.,;') for token in tokens if token.strip('.,;') and token.strip('.,;') not in _JUNK_WORDS]
    if not tokens and not forced_sides:
        return None
    found = None
    for token in tokens:
        found = _find_lemma(token)
        if found:
            break
    if not found:
        return None
    noun, gender, default_position = found
    sides = list(forced_sides) or _sides_in_text(' '.join(tokens))
    position = _position_in_text(' '.join(tokens)) or default_position
    if not sides:
        return [_compose_part('', position, noun, gender)]
    return [_compose_part(side, position, noun, gender) for side in sides]


def _find_lemma(token: str) -> tuple[str, str, str] | None:
    for lemma, noun, gender, default_position in _PART_LEMMAS:
        if token == lemma:
            return noun, gender, default_position
    return None


def _sides_in_text(text: str) -> list[str]:
    sides: list[str] = []
    if re.search(r'прав', text):
        sides.append('right')
    if re.search(r'лев', text):
        sides.append('left')
    return sides


def _has_side(text: str) -> bool:
    return bool(_sides_in_text(text.casefold()))


def _position_in_text(text: str) -> str:
    if re.search(r'задн', text):
        return 'rear'
    if re.search(r'передн', text):
        return 'front'
    return ''


def _has_position(text: str) -> bool:
    return bool(_position_in_text(text.casefold()))


def _compose_part(side: str, position: str, noun: str, gender: str) -> str:
    side_word = _agree_side(side, gender)
    position_word = _agree_position(position, gender)
    words = [word for word in (side_word, position_word, noun.casefold()) if word]
    phrase = ' '.join(words)
    if not phrase:
        return ''
    return phrase[0].upper() + phrase[1:]


def _agree_side(side: str, gender: str) -> str:
    forms = {
        'right': {'f': 'Правая', 'm': 'Правый', 'n': 'Правое', 'p': 'Правые'},
        'left': {'f': 'Левая', 'm': 'Левый', 'n': 'Левое', 'p': 'Левые'},
    }
    return forms.get(side, {}).get(gender, '')


def _agree_position(position: str, gender: str) -> str:
    forms = {
        'front': {'f': 'передняя', 'm': 'передний', 'n': 'переднее', 'p': 'передние'},
        'rear': {'f': 'задняя', 'm': 'задний', 'n': 'заднее', 'p': 'задние'},
    }
    return forms.get(position, {}).get(gender, '')


def _title_phrase(value: str) -> str:
    cleaned = _clean(value)
    if not cleaned:
        return ''
    return cleaned[0].upper() + cleaned[1:]


def _public_contains_pii(public: PublicPartRequest, product_request) -> bool:
    chunks = [
        public.brand,
        public.model,
        public.year,
        public.city,
        public.generation,
        *public.parts,
        *public.notes,
    ]
    blob = '\n'.join(chunks)
    if _PII_RE.search(blob):
        return True
    phone = _clean(getattr(product_request, 'phone', ''))
    vin = _clean(getattr(product_request, 'vin', ''))
    for secret in (phone, vin):
        if secret and secret in blob:
            return True
    for attr in ('access_token', 'short_token'):
        token = str(getattr(product_request, attr, '') or '').strip()
        if token and token in blob:
            return True
    return False
