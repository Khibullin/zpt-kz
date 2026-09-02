"""Public-field contract, sanitizer, and product-card quality audit.

Does not write Product rows. Cleanup commands call apply_safe_fixes explicitly.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from catalog.applicability import parse_plain_list, serialize_plain_list
from catalog.article_utils import normalize_article
from catalog.models import Category, Product

STATUS_OK = 'OK'
STATUS_AUTO_FIXABLE = 'AUTO_FIXABLE'
STATUS_MANUAL = 'MANUAL_REVIEW'
STATUS_CRITICAL = 'CRITICAL'

SAFE_STATUS_LABEL = 'SAFE'
MANUAL_STATUS_LABEL = 'MANUAL'

PUBLIC_TEXT_FIELDS = (
    'title',
    'article',
    'compatibility',
    'engine_compatibility',
    'oem_cross_references',
    'description',
)

INTERNAL_RESEARCH_ERROR = (
    'В описании или применимости присутствует служебный текст. '
    'Удалите внутренние заметки перед сохранением.'
)

RESEARCH_MARKER_PATTERNS = (
    re.compile(r'справочник[еа]?\s+zpt', re.I),
    re.compile(r'zpt\s+нет', re.I),
    re.compile(r'в\s+справочнике\s+zpt', re.I),
    re.compile(r'у\s+поставщиков', re.I),
    re.compile(r'\bпоставщик', re.I),
    re.compile(r'внешн\w*\s+каталог', re.I),
    re.compile(r'во\s+внешних\s+каталог', re.I),
    re.compile(r'\bкаталогах\b', re.I),
    re.compile(r'fitinpart', re.I),
    re.compile(r'отвергнут', re.I),
    re.compile(r'не\s+включ', re.I),
    re.compile(r'\bритейл', re.I),
    re.compile(r'\bretail\b', re.I),
    re.compile(r'confirmed\s+by', re.I),
    re.compile(r'подтвержд[её]н\w*\s+каталог', re.I),
    re.compile(r'\bgemini\b', re.I),
    re.compile(r'chatgpt', re.I),
    re.compile(r'whatsapp\s+image', re.I),
    re.compile(r'чат\s+с\s+gemini', re.I),
    re.compile(r'файл[ае]?\s+\S+\.(?:jpe?g|png|webp)', re.I),
    re.compile(r'\.(?:jpe?g|png|webp)\b', re.I),
    re.compile(r'судя\s+по\s+этикетке', re.I),
    re.compile(r'\bя\s+нашёл\b', re.I),
    re.compile(r'поиск\s+показал', re.I),
    re.compile(r'\bисточник', re.I),
    re.compile(r'в\s+карточке\s+zpt', re.I),
    re.compile(r'по\s+данным\s+поставщиков', re.I),
    re.compile(r'подтверждено\s+каталогами', re.I),
    re.compile(r'у\s+аналогов', re.I),
    re.compile(r'встречается\s+у\s+поставщиков', re.I),
    re.compile(r'есть\s+у\s+поставщиков', re.I),
    re.compile(r'есть\s+во\s+внешних', re.I),
    re.compile(r'\bchatgpt\b|\bopenai\b|\bclaude\b', re.I),
)

_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+|\n+')
_OEM_LABEL = re.compile(
    r'(?i)(?:'
    r'\b(?:oem|кросс|cross(?:[-\s]?ref(?:erence)?s?)?|аналог(?:и|ов)?|подтвержд\w*)\s*:'
    r'|'
    r'\b(?:oem[-\s]номер(?:а|ы)?|кросс[-\s]?номер(?:а|ы)?)\b[:\s]*'
    r')'
)
_OEM_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{1,40}')
_OEM_SKIP_TOKENS = frozenset({
    'oem', 'cross', 'ref', 'reference', 'references', 'xx',
    'ag', 'eco', 'sa', 'sb', 'hd', 'sx', 'oe', 'pc',
})
_OEM_COMPACT_RE = re.compile(r'[^A-Za-z0-9]')
_ENGINE_TOKEN = re.compile(
    r'(?i)\b(?:\d+(?:[.,]\d+)?\s*(?:t|l|turbo|gdi|tgdi|tdi|mpi|dci)?|'
    r'[A-Z]{1,5}\d[A-Z0-9]{1,8})\b'
)
_WHITESPACE = re.compile(r'[ \t]+')
_MULTI_NL = re.compile(r'\n{3,}')

_ARTICLE_WITH_OEM = re.compile(
    r'^(?P<article>[A-Za-z0-9][A-Za-z0-9._/-]{0,48})\s*'
    r'(?:\(|\[)?\s*'
    r'(?:кросс[-\s]?номер(?:а|ы)?(?:\s+OEM)?|OEM(?:[-\s]?номер(?:а|ы)?)?|'
    r'оригинальный\s+кросс[-\s]?номер)'
    r'\s*:?\s*(?P<oem>.+?)\)?\s*$',
    re.I | re.S,
)
_ARTICLE_PREFIX = re.compile(
    r'^(?:артикул\s*:?\s*)(?P<article>[A-Za-z0-9][A-Za-z0-9._/-]{1,48})\s+'
    r'(?:оригинальный\s+)?(?:кросс|oem).+$',
    re.I | re.S,
)
_OEM_ONLY_LABEL = re.compile(
    r'^(?:oem[-\s]?номер(?:а|ы)?|кросс[-\s]?номер(?:а|ы)?)\b',
    re.I,
)

GENERIC_DESCRIPTION_MARKERS = (
    'цены всегда за 1 шт',
    'оригинальные запчасти на toyota и lexus',
    'оригинальные запчасти на toyota',
)

PART_TYPE_PATTERNS = (
    ('наконечник', 'tie_rod_end'),
    ('амортизатор', 'shock'),
    ('сайлентблок', 'silentblock'),
    ('пыльник шруса', 'cv_boot'),
    ('пыльник', 'boot'),
    ('рулевая тяга', 'tie_rod'),
    ('тяга рулев', 'tie_rod'),
    ('свеч', 'spark'),
    ('фильтр салон', 'cabin_filter'),
    ('фильтр масля', 'oil_filter'),
    ('фильтр воздуш', 'air_filter'),
    ('фильтр топлив', 'fuel_filter'),
    ('фильтр', 'filter'),
    ('рычаг', 'arm'),
    ('колодк', 'brake_pad'),
    ('диск тормоз', 'brake_disc'),
    ('ступиц', 'hub'),
    ('шаров', 'ball_joint'),
    ('ремень', 'belt'),
    ('помп', 'pump'),
    ('радиатор', 'radiator'),
    ('фара', 'headlight'),
    ('бампер', 'bumper'),
)

# Morphological / family equivalents: do not treat as different part types.
PART_TYPE_FAMILY = {
    'boot': 'boot',
    'cv_boot': 'boot',
}

_YO_FOLD = str.maketrans({'ё': 'е', 'Ё': 'е'})
_CV_BOOT_STEM = re.compile(r'пыльник', re.I)
_CV_JOINT_STEM = re.compile(r'шру[сc]', re.I)

SPARK_CATEGORY_NAMES = (
    'Свечи зажигания',
    'Система зажигания',
)

# General category name (casefold) → part type that has a more specific Category.
BROAD_CATEGORY_GENERAL = {
    'электрика': 'spark',
}

SPECIALIZED_CATEGORY_FOR_TYPE = {
    'spark': SPARK_CATEGORY_NAMES,
}

_STANDALONE_OEM_LINE = re.compile(
    r'(?i)^\s*(?:'
    r'oem(?:[-\s]номер(?:а|ы)?)?'
    r'|кросс(?:[-\s]?номер(?:а|ы)?)?'
    r'|cross(?:[-\s]?ref(?:erence)?s?)?'
    r')\s*:.*$'
)
_MANUFACTURER_ARTICLE_RE = re.compile(
    r'(?:'
    r'артикул\s*\(\s*номер\s+производителя\s*\)|'
    r'артикул\s+производителя|'
    r'номер\s+производителя|'
    r'manufacturer(?:\s+(?:article|part(?:\s*number)?|pn))?|'
    r'part\s*number|'
    r'\bp/?n\b'
    r')\s*:?\s*'
    r'([A-Za-z0-9][A-Za-z0-9._/-]{2,40})',
    re.I,
)
_CROSS_NUMBER_LABEL_RE = re.compile(
    r'(?:кросс[-\s]?номер(?:а|ы)?(?:\s*\([^)]+\))?|'
    r'cross[-\s]?(?:number|ref(?:erence)?s?))'
    r'\s*:?\s*'
    r'([A-Za-z0-9][A-Za-z0-9._/-]{2,40})',
    re.I,
)
_COMPATIBILITY_METADATA_PATTERNS = (
    re.compile(r'основные\s+данные\s+и\s+артикул', re.I),
    re.compile(r'^\s*наименование\s*:', re.I | re.M),
    re.compile(r'артикул\s*\(\s*номер\s+производителя\s*\)\s*:', re.I),
    re.compile(r'артикул\s+производителя\s*:', re.I),
    re.compile(r'кросс[-\s]?номер\s*\(\s*артикул\s+аналога\s*\)\s*:', re.I),
    re.compile(r'номер\s+производителя\s*:', re.I),
    re.compile(r'технические\s+данные', re.I),
)
_MIN_DEDUPE_CHARS = 20
_MIN_BLOCK_DEDUPE_CHARS = 60
_URL_RE = re.compile(r'https?://[^\s)>\]]+', re.I)
KNOWN_SELLER_MARKETPLACE_DOMAINS = (
    'grm4x4.kz',
    'emex.ru',
    'exist.ru',
    'autodoc.ru',
    'drom.ru',
    'avito.ru',
    'olx.kz',
    'olx.ru',
    'kaspi.kz',
    'wildberries.ru',
    'ozon.ru',
    'market.yandex.ru',
    'alibaba.com',
    '1688.com',
)


@dataclass
class ParsedArticle:
    primary: str
    oem_numbers: list[str]
    confident: bool
    raw: str


@dataclass
class SanitizeResult:
    text: str
    removed: list[str] = field(default_factory=list)
    changed: bool = False
    safe_to_apply: bool = False


@dataclass
class ProductAuditResult:
    product_id: int
    seller_name: str
    title: str
    article: str
    status: str
    severity: str
    issues: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    safe_fixes: dict[str, str] = field(default_factory=dict)

    def as_csv_row(self) -> dict[str, str]:
        return {
            'product_id': str(self.product_id),
            'seller_name': self.seller_name,
            'title': self.title,
            'article': self.article,
            'status': self.status,
            'severity': self.severity,
            'issues': '; '.join(self.issues),
            'suggested_actions': '; '.join(self.suggested_actions),
        }


def detect_internal_research_text(value: str | None) -> list[str]:
    text = str(value or '')
    if not text.strip():
        return []
    hits = []
    seen = set()
    for pattern in RESEARCH_MARKER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        token = match.group(0)
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        hits.append(token)
    return hits


def split_public_sentences(value: str) -> list[str]:
    text = str(value or '').strip()
    if not text:
        return []
    parts = [item.strip() for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    return parts or [text]


def _normalize_whitespace(value: str) -> str:
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    text = _WHITESPACE.sub(' ', text)
    text = _MULTI_NL.sub('\n\n', text)
    lines = [line.strip() for line in text.split('\n')]
    return '\n'.join(line for line in lines if line).strip()


def oem_raw_tokens(value: str | None) -> list[str]:
    text = _OEM_LABEL.sub(' ', str(value or ''))
    tokens: list[str] = []
    seen: set[str] = set()
    for token in _OEM_TOKEN.findall(text):
        token = token.strip().strip('.,;:()[]')
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token)
    return tokens


def oem_compact(value: str | None) -> str:
    return _OEM_COMPACT_RE.sub('', str(value or '')).upper()


def is_plausible_oem_token(token: str | None) -> bool:
    """Keep part-number-like tokens; drop brand fragments and short generics."""
    raw = str(token or '').strip().strip('.,;:()[]')
    if not raw:
        return False
    compact = oem_compact(raw)
    if not compact or compact.casefold() in _OEM_SKIP_TOKENS:
        return False
    if len(compact) < 7:
        return False
    has_letter = bool(re.search(r'[A-Za-z]', compact))
    has_digit = bool(re.search(r'\d', compact))
    if not has_letter or not has_digit:
        return False
    if len(re.findall(r'\d', compact)) < 3:
        return False
    if detect_internal_research_text(raw):
        return False
    return True


def sanitize_oem_text(value: str | None, *, queried_article: str | None = '') -> str:
    text = _OEM_LABEL.sub(' ', str(value or ''))
    queried_key = oem_compact(queried_article)
    tokens: list[str] = []
    seen: set[str] = set()
    for token in _OEM_TOKEN.findall(text):
        token = token.strip().strip('.,;:()[]')
        if not token or detect_internal_research_text(token):
            continue
        key = token.casefold()
        if key in seen or key in _OEM_SKIP_TOKENS:
            continue
        if not is_plausible_oem_token(token):
            continue
        if queried_key and oem_compact(token) == queried_key:
            continue
        seen.add(key)
        tokens.append(token)
    return '\n'.join(tokens)


def sanitize_oem_research(value: str | None, *, queried_article: str | None = '') -> tuple[str, str]:
    """Drop fragmented OEM lists; return (public_text, rejected_blob)."""
    raw = str(value or '')
    tokens = oem_raw_tokens(raw)
    kept = sanitize_oem_text(raw, queried_article=queried_article)
    kept_items = [item for item in kept.split('\n') if item]
    queried_key = oem_compact(queried_article)
    rejected = [
        token for token in tokens
        if not is_plausible_oem_token(token) and oem_compact(token) != queried_key
    ]
    if tokens and not kept_items:
        return '', ' | '.join(tokens)
    if rejected and len(rejected) >= 3 and len(kept_items) <= 1:
        return '', ' | '.join(tokens)
    return kept, ''


def sanitize_engine_text(value: str | None) -> str:
    clean_sentences = []
    for sentence in split_public_sentences(value or ''):
        if detect_internal_research_text(sentence):
            continue
        clean_sentences.append(sentence)
    leftover = '\n'.join(clean_sentences)
    items = parse_plain_list(leftover)
    if items:
        return serialize_plain_list('\n'.join(items))
    return _normalize_whitespace(leftover)


def _field_sanitize(value: str, field: str) -> SanitizeResult:
    original = str(value or '')
    if field == 'oem_cross_references':
        cleaned = sanitize_oem_text(original)
        changed = _normalize_whitespace(original) != cleaned
        return SanitizeResult(
            text=cleaned,
            removed=[],
            changed=changed,
            safe_to_apply=changed and bool(cleaned),
        )
    if field == 'engine_compatibility':
        cleaned = sanitize_engine_text(original)
        removed = [
            sentence for sentence in split_public_sentences(original)
            if detect_internal_research_text(sentence)
        ]
        leftover_ok = bool(cleaned) and not detect_internal_research_text(cleaned)
        return SanitizeResult(
            text=cleaned,
            removed=removed,
            changed=_normalize_whitespace(original) != cleaned,
            safe_to_apply=bool(removed) and leftover_ok,
        )
    if field == 'article':
        parsed = parse_article_and_oem(original)
        cleaned = parsed.primary if parsed.confident else _normalize_whitespace(original)
        return SanitizeResult(
            text=cleaned[:100],
            removed=[],
            changed=cleaned != original.strip(),
            safe_to_apply=parsed.confident and bool(parsed.primary),
        )

    removed = []
    kept = []
    for sentence in split_public_sentences(original):
        if detect_internal_research_text(sentence):
            removed.append(sentence)
            continue
        kept.append(sentence)
    cleaned = _normalize_whitespace('\n'.join(kept))
    leftover_ok = bool(cleaned) and not detect_internal_research_text(cleaned)
    safe = bool(removed) and leftover_ok
    if field == 'compatibility' and removed and leftover_ok:
        # Remaining text must look like standalone fitment, not a fragment.
        if len(cleaned) < 4:
            safe = False
    return SanitizeResult(
        text=cleaned,
        removed=removed,
        changed=_normalize_whitespace(original) != cleaned,
        safe_to_apply=safe,
    )


def sanitize_public_product_text(
    value: str | None,
    *,
    field: str = 'description',
    mode: str = 'preview',
) -> str:
    """Strip internal research sentences from a public field.

    mode=preview|apply always drops dirty sentences.
    mode=production only returns a replacement when leftover is independent.
    """
    result = _field_sanitize(value or '', field)
    if mode == 'production' and not result.safe_to_apply:
        return _normalize_whitespace(value or '')
    return result.text


def parse_article_and_oem(value: str | None) -> ParsedArticle:
    raw = str(value or '').strip()
    if not raw:
        return ParsedArticle(primary='', oem_numbers=[], confident=False, raw=raw)
    if detect_internal_research_text(raw) and not _ARTICLE_WITH_OEM.match(raw):
        return ParsedArticle(primary=raw[:100], oem_numbers=[], confident=False, raw=raw)

    oem_match = _ARTICLE_WITH_OEM.match(raw)
    prefix_match = _ARTICLE_PREFIX.match(raw)
    match = oem_match or prefix_match
    if match:
        primary = (match.group('article') or '').strip()
        oem_raw = ''
        if 'oem' in match.groupdict() and match.groupdict().get('oem'):
            oem_raw = match.group('oem')
        else:
            oem_raw = raw[len(primary):]
        oem_numbers = sanitize_oem_text(oem_raw).split('\n') if oem_raw else []
        oem_numbers = [item for item in oem_numbers if item and item.casefold() != primary.casefold()]
        confident = bool(primary) and oem_match is not None and not _OEM_ONLY_LABEL.match(raw)
        return ParsedArticle(
            primary=primary[:100],
            oem_numbers=oem_numbers,
            confident=confident,
            raw=raw,
        )
    if _OEM_ONLY_LABEL.match(raw) or 'кросс-номер' in raw.casefold() or 'oem-номер' in raw.casefold():
        oem_numbers = [item for item in sanitize_oem_text(raw).split('\n') if item]
        return ParsedArticle(
            primary='',
            oem_numbers=oem_numbers,
            confident=False,
            raw=raw,
        )
    return ParsedArticle(primary=raw[:100], oem_numbers=[], confident=False, raw=raw)


def prefer_public_field(local_value: str, incoming_value: str, *, field: str) -> str:
    """Clean local text wins. Dirty local never blocks a clean AI replacement."""
    local_raw = str(local_value or '')
    incoming_raw = str(incoming_value or '')
    incoming_clean = sanitize_public_product_text(incoming_raw, field=field, mode='preview')
    if detect_internal_research_text(local_raw):
        return incoming_clean
    local_clean = sanitize_public_product_text(local_raw, field=field, mode='preview')
    if local_clean.strip():
        return local_clean
    return incoming_clean


def research_notes_from_removed(removed: Iterable[str], *, severity: str = 'warning') -> list[dict[str, str]]:
    notes = []
    seen = set()
    for item in removed:
        text = _normalize_whitespace(str(item or ''))
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        notes.append({'text': text, 'severity': severity})
    return notes


def normalize_research_notes(raw) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    if not raw:
        return notes
    items = raw if isinstance(raw, list) else [raw]
    for item in items:
        if isinstance(item, str):
            text = item.strip()
            severity = 'info'
        elif isinstance(item, dict):
            text = str(item.get('text') or '').strip()
            severity = str(item.get('severity') or 'info').strip() or 'info'
        else:
            continue
        if not text:
            continue
        if severity not in {'info', 'warning', 'critical'}:
            severity = 'info'
        notes.append({'text': text, 'severity': severity})
    return notes


def part_type_from_text(value: str | None) -> str:
    text = str(value or '').casefold().translate(_YO_FOLD)
    if not text:
        return ''
    if _CV_BOOT_STEM.search(text) and _CV_JOINT_STEM.search(text):
        return 'cv_boot'
    for needle, code in PART_TYPE_PATTERNS:
        if needle in text:
            return code
    return ''


def part_type_family(code: str | None) -> str:
    if not code:
        return ''
    return PART_TYPE_FAMILY.get(code, code)


def part_types_conflict(type_a: str | None, type_b: str | None) -> bool:
    if not type_a or not type_b or type_a == type_b:
        return False
    return part_type_family(type_a) != part_type_family(type_b)


def detect_article_role_unclear(article: str | None, *texts: str) -> bool:
    """True when Product.article is labeled as a cross-number and another PN exists."""
    raw = str(article or '').strip()
    key = normalize_article(raw)
    if not key:
        return False
    blob = '\n'.join(str(item or '') for item in texts)
    if not blob.strip():
        return False
    cross_keys = {normalize_article(item) for item in _CROSS_NUMBER_LABEL_RE.findall(blob)}
    mfr_keys = {normalize_article(item) for item in _MANUFACTURER_ARTICLE_RE.findall(blob)}
    cross_keys.discard('')
    mfr_keys.discard('')
    if key not in cross_keys:
        return False
    return any(item != key for item in mfr_keys)


def is_compatibility_metadata_block(value: str | None) -> bool:
    """True when compatibility is a product metadata block, not vehicle fitment."""
    text = str(value or '').strip()
    if not text:
        return False
    hits = sum(1 for pattern in _COMPATIBILITY_METADATA_PATTERNS if pattern.search(text))
    return hits >= 2 or (hits >= 1 and not _looks_like_vehicle_fitment(text))


def _looks_like_vehicle_fitment(value: str) -> bool:
    text = str(value or '')
    if re.search(
        r'\b(?:toyota|hyundai|kia|changan|chery|geely|haval|gwm|bmw|audi|'
        r'mercedes|volkswagen|nissan|honda|mazda|lexus|subaru|mitsubishi|'
        r'uni-k|camry|sonata|tiggo|cs\d{2})\b',
        text,
        re.I,
    ):
        return True
    if re.search(r'\b\d{4}\s*[-–—]\s*\d{4}\b', text):
        return True
    return False


def _related_manager_has(product: Product, field_name: str) -> bool:
    manager = getattr(product, field_name, None)
    if manager is None:
        return False
    prefetched = getattr(product, '_prefetched_objects_cache', None)
    if isinstance(prefetched, dict) and field_name in prefetched:
        return bool(prefetched[field_name])
    return manager.exists()


def _has_brand(product: Product) -> bool:
    return bool(product.brand_id) or _related_manager_has(product, 'selected_brands')


def _has_model_applicability(product: Product) -> bool:
    compatibility = product.compatibility or ''
    if compatibility.strip() and not is_compatibility_metadata_block(compatibility):
        return True
    if product.car_model_id:
        return True
    return _related_manager_has(product, 'selected_models')


def _category_name_set(names: Iterable[str] | None) -> set[str]:
    if names is not None:
        return {str(item) for item in names if str(item).strip()}
    return set(Category.objects.values_list('name', flat=True))


def has_front_rear_conflict(titles: Iterable[str]) -> bool:
    blob = ' '.join(str(item or '') for item in titles).casefold()
    front = any(token in blob for token in ('передн', 'front'))
    rear = any(token in blob for token in ('задн', 'rear'))
    return front and rear


def is_generic_seller_description(description: str, *, title: str = '', article: str = '') -> bool:
    text = str(description or '').strip()
    if not text:
        return False
    folded = text.casefold()
    if any(marker in folded for marker in GENERIC_DESCRIPTION_MARKERS):
        return True
    if len(text) > 280 and 'цены' in folded and 'товар' in folded:
        title_key = str(title or '').casefold()
        article_key = str(article or '').casefold()
        mentions_item = bool(title_key and title_key[:12] in folded)
        mentions_article = bool(article_key and article_key in folded)
        if not mentions_item and not mentions_article:
            return True
    return False


def _url_hostname(url: str) -> str:
    match = re.match(r'https?://([^/:]+)', str(url or ''), re.I)
    if not match:
        return ''
    host = match.group(1).strip().casefold()
    if host.startswith('www.'):
        host = host[4:]
    return host


def detect_external_seller_links(text: str | None, *, seller_name: str = '') -> list[str]:
    """Known marketplace/other-seller domains only. Manufacturer URLs are ignored."""
    seller_key = str(seller_name or '').casefold()
    hits: list[str] = []
    seen: set[str] = set()
    for raw in _URL_RE.findall(str(text or '')):
        host = _url_hostname(raw)
        if not host:
            continue
        for domain in KNOWN_SELLER_MARKETPLACE_DOMAINS:
            if host != domain and not host.endswith('.' + domain):
                continue
            shop = domain.split('.')[0]
            if shop and shop in seller_key:
                continue
            if domain in seen:
                continue
            seen.add(domain)
            hits.append(domain)
    return hits


def is_malformed_description(value: str | None) -> bool:
    text = str(value or '')
    if not text.strip():
        return False
    unclosed = text.count('(') - text.count(')')
    ellipses = len(re.findall(r'\.{3}|…', text))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    truncated_fitment = 0
    for line in lines:
        if not re.search(r'\d{4}', line):
            continue
        if re.search(r'\(\s*\d{1,2}\.\d{4}|\(\s*\d{4}', line) and (
            'н.' in line.casefold() or '...' in line or '…' in line
        ):
            truncated_fitment += 1
    if unclosed >= 3 and (ellipses >= 3 or truncated_fitment >= 3):
        return True
    if truncated_fitment >= 5 and ellipses >= 3:
        return True
    if unclosed >= 5:
        return True
    return False


def _trim_fix(value: str) -> str:
    return _normalize_whitespace(value)


def _sentence_dedupe_key(value: str) -> str:
    return _WHITESPACE.sub(' ', str(value or '')).strip().casefold()


def _strip_exact_repeated_block(text: str) -> str | None:
    """If text is an exact consecutive A+A block, return A (+ leftover)."""
    source = str(text or '')
    n = len(source)
    if n < _MIN_BLOCK_DEDUPE_CHARS * 2:
        return None
    for length in range(n // 2, _MIN_BLOCK_DEDUPE_CHARS - 1, -1):
        if source[:length] != source[length:length + length]:
            continue
        remainder = source[length + length:]
        return (source[:length] + remainder).strip()
    return None


def _dedupe_public_sentences(value: str) -> str:
    """Drop exact duplicate lines/blocks/sentences. Keep original layout if none."""
    original = str(value or '')
    if not original.strip():
        return original
    light = _normalize_whitespace(original)
    blocked = _strip_exact_repeated_block(light)
    if blocked is not None:
        return _normalize_whitespace(blocked)

    lines = light.split('\n')
    kept_lines: list[str] = []
    seen_lines: set[str] = set()
    dropped_line = False
    for line in lines:
        key = _sentence_dedupe_key(line)
        if len(key) >= _MIN_DEDUPE_CHARS and key in seen_lines:
            dropped_line = True
            continue
        if len(key) >= _MIN_DEDUPE_CHARS:
            seen_lines.add(key)
        kept_lines.append(line)
    if dropped_line:
        return '\n'.join(kept_lines)

    new_lines: list[str] = []
    changed = False
    for line in kept_lines:
        sentences = split_public_sentences(line)
        kept_sentences: list[str] = []
        seen_sentences: set[str] = set()
        line_dropped = False
        for sentence in sentences:
            key = _sentence_dedupe_key(sentence)
            if len(key) >= _MIN_DEDUPE_CHARS and key in seen_sentences:
                line_dropped = True
                continue
            if len(key) >= _MIN_DEDUPE_CHARS:
                seen_sentences.add(key)
            kept_sentences.append(sentence)
        if not line_dropped:
            new_lines.append(line)
            continue
        changed = True
        joiner = ' '
        new_lines.append(joiner.join(kept_sentences))
    if not changed:
        return light
    return _normalize_whitespace('\n'.join(new_lines))


def _drop_duplicate_oem_lines(description: str, oem_text: str) -> str:
    oem_tokens = {
        item.casefold()
        for item in sanitize_oem_text(oem_text).split('\n')
        if item
    }
    if not oem_tokens or not (description or '').strip():
        return description
    kept: list[str] = []
    dropped = False
    for line in str(description).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _STANDALONE_OEM_LINE.match(stripped):
            line_tokens = {
                item.casefold()
                for item in sanitize_oem_text(stripped).split('\n')
                if item
            }
            if line_tokens and line_tokens <= oem_tokens:
                dropped = True
                continue
        kept.append(stripped)
    if not dropped:
        return description
    leftover = '\n'.join(kept)
    if not leftover.strip():
        return description
    return leftover


def _working_public_fields(product: Product) -> dict[str, str]:
    """Canonical public-text snapshot. Same input always yields the same dict."""
    current_article = product.article or ''
    parsed = parse_article_and_oem(current_article)
    if parsed.confident and parsed.primary:
        article_working = parsed.primary[:100]
    else:
        article_working = _normalize_whitespace(current_article)

    oem_working = sanitize_oem_text(product.oem_cross_references or '')
    if parsed.confident and parsed.oem_numbers:
        oem_working = sanitize_oem_text(oem_working + '\n' + '\n'.join(parsed.oem_numbers))

    working: dict[str, str] = {
        'article': article_working,
        'oem_cross_references': oem_working,
    }
    for name in ('title', 'engine_compatibility'):
        current = getattr(product, name, '') or ''
        result = _field_sanitize(current, name)
        working[name] = result.text if result.safe_to_apply else _normalize_whitespace(current)

    compatibility_current = product.compatibility or ''
    if is_compatibility_metadata_block(compatibility_current):
        working['compatibility'] = compatibility_current
    else:
        compat_result = _field_sanitize(compatibility_current, 'compatibility')
        working['compatibility'] = (
            compat_result.text if compat_result.safe_to_apply
            else _normalize_whitespace(compatibility_current)
        )

    desc_current = product.description or ''
    if is_malformed_description(desc_current):
        working['description'] = desc_current
        return working
    desc_result = _field_sanitize(desc_current, 'description')
    desc_working = desc_result.text if desc_result.safe_to_apply else _normalize_whitespace(desc_current)
    desc_working = _drop_duplicate_oem_lines(desc_working, oem_working)
    desc_working = _dedupe_public_sentences(desc_working)
    working['description'] = _normalize_whitespace(desc_working)
    return working


def _collect_safe_fixes(product: Product) -> dict[str, str]:
    working = _working_public_fields(product)
    fixes: dict[str, str] = {}
    for name in PUBLIC_TEXT_FIELDS:
        current = getattr(product, name, '') or ''
        value = working.get(name, current)
        if value == current:
            continue
        if name == 'compatibility' and is_compatibility_metadata_block(current):
            continue
        if name == 'description' and is_malformed_description(current):
            continue
        if not value and current.strip() and name in {
            'title',
            'article',
            'compatibility',
            'description',
        }:
            continue
        fixes[name] = value
    return fixes


def _status_rank(status: str) -> int:
    return {
        STATUS_OK: 0,
        STATUS_AUTO_FIXABLE: 1,
        STATUS_MANUAL: 2,
        STATUS_CRITICAL: 3,
    }.get(status, 0)


def audit_product(
    product: Product,
    *,
    article_groups: dict[str, list[Product]] | None = None,
    generic_descriptions: set[str] | None = None,
    category_names: Iterable[str] | None = None,
) -> ProductAuditResult:
    issues: list[str] = []
    actions: list[str] = []
    status = STATUS_OK
    severity = 'info'

    def bump(new_status: str, issue: str, action: str = '', *, sev: str = ''):
        nonlocal status, severity
        issues.append(issue)
        if action:
            actions.append(action)
        if _status_rank(new_status) > _status_rank(status):
            status = new_status
        wanted = sev or (
            'critical' if new_status == STATUS_CRITICAL
            else 'warning' if new_status == STATUS_MANUAL
            else 'info'
        )
        rank = {'info': 0, 'warning': 1, 'critical': 2}
        if rank.get(wanted, 0) > rank.get(severity, 0):
            severity = wanted

    title_type = part_type_from_text(product.title)
    description_type = part_type_from_text(product.description)
    if part_types_conflict(title_type, description_type):
        bump(
            STATUS_CRITICAL,
            'CRITICAL_MANUAL_REVIEW: title и description относятся к разным типам деталей',
            'Не исправлять применимость автоматически. Проверить карточку вручную.',
            sev='critical',
        )
    if detect_internal_research_text(product.description) and (
        re.search(r'чат\s+с|gemini|chatgpt|whatsapp\s+image', product.description or '', re.I)
        or len(product.description or '') > 4000
    ):
        bump(
            STATUS_CRITICAL,
            'CRITICAL: сырой AI/чат в описании',
            'Очистить description вручную. Не использовать как применимость.',
            sev='critical',
        )

    for name in ('title', 'compatibility', 'description', 'engine_compatibility', 'oem_cross_references'):
        value = getattr(product, name, '') or ''
        hits = detect_internal_research_text(value)
        if not hits:
            continue
        result = _field_sanitize(value, name)
        if result.safe_to_apply:
            bump(
                STATUS_AUTO_FIXABLE,
                f'INTERNAL_TEXT:{name}',
                f'Безопасно убрать служебные фразы из {name}.',
            )
        else:
            bump(
                STATUS_MANUAL,
                f'INTERNAL_TEXT:{name}',
                f'Проверить {name}: служебный текст нельзя удалить без потери смысла.',
            )

    parsed = parse_article_and_oem(product.article or '')
    if parsed.confident and (parsed.oem_numbers or parsed.primary != (product.article or '').strip()):
        bump(
            STATUS_AUTO_FIXABLE,
            'MALFORMED_ARTICLE',
            'Вынести OEM из article в oem_cross_references.',
        )
    elif (product.article or '').strip() and (
        _OEM_ONLY_LABEL.match(product.article or '')
        or 'кросс-номер' in (product.article or '').casefold()
        or 'oem-номер' in (product.article or '').casefold()
        or 'артикул:' in (product.article or '').casefold()
    ):
        bump(
            STATUS_MANUAL,
            'MALFORMED_ARTICLE',
            'Parser не уверен в primary article — проверить вручную.',
        )

    if is_generic_seller_description(
        product.description or '',
        title=product.title or '',
        article=product.article or '',
    ):
        bump(
            STATUS_MANUAL,
            'GENERIC_SELLER_DESCRIPTION',
            'Описание магазина вместо описания детали.',
        )
    folded_desc = (product.description or '').strip().casefold()
    if generic_descriptions and folded_desc and folded_desc in generic_descriptions:
        bump(
            STATUS_MANUAL,
            'GENERIC_SELLER_DESCRIPTION',
            'Одинаковое описание у нескольких карточек продавца.',
        )

    seller_name_for_links = ''
    if getattr(product, 'seller_profile', None) and product.seller_profile:
        seller_name_for_links = product.seller_profile.name
    else:
        seller_name_for_links = product.seller_name or ''
    if detect_external_seller_links(product.description or '', seller_name=seller_name_for_links):
        bump(
            STATUS_MANUAL,
            'EXTERNAL_SELLER_LINK',
            'В описании ссылка на другой магазин/маркетплейс. Не удалять автоматически.',
        )

    if is_malformed_description(product.description):
        bump(
            STATUS_MANUAL,
            'MALFORMED_DESCRIPTION',
            'Описание с незакрытыми скобками или обрывками применимости. Не восстанавливать годы автоматически.',
        )

    if detect_article_role_unclear(
        product.article,
        product.compatibility or '',
        product.description or '',
        product.oem_cross_references or '',
        product.title or '',
    ):
        bump(
            STATUS_MANUAL,
            'ARTICLE_ROLE_UNCLEAR',
            'Текущий article похож на кросс-номер, а в карточке указан другой артикул производителя. Не переписывать автоматически.',
        )

    if is_compatibility_metadata_block(product.compatibility):
        bump(
            STATUS_MANUAL,
            'COMPATIBILITY_NOT_FITMENT',
            'В применимости записан блок артикулов/наименования, а не автомобили. Не исправлять автоматически.',
        )

    if not (product.article or '').strip():
        bump(STATUS_MANUAL, 'MISSING_ARTICLE', 'Указать артикул.')
    if not _has_brand(product):
        bump(STATUS_MANUAL, 'MISSING_BRAND', 'Указать марку.')
    if not product.category_id:
        bump(STATUS_MANUAL, 'MISSING_CATEGORY', 'Указать категорию.')
    if not _has_model_applicability(product):
        bump(STATUS_MANUAL, 'MISSING_COMPATIBILITY', 'Указать применимость.')
    if not (product.description or '').strip():
        bump(STATUS_MANUAL, 'MISSING_DESCRIPTION', 'Добавить описание детали.')

    known_categories = _category_name_set(category_names)
    category_name = (product.category.name if product.category_id else '').casefold()
    expected_type = BROAD_CATEGORY_GENERAL.get(category_name)
    if expected_type and title_type == expected_type:
        specialized = SPECIALIZED_CATEGORY_FOR_TYPE.get(expected_type, ())
        existing_fold = {name.casefold() for name in known_categories}
        if any(name.casefold() in existing_fold for name in specialized):
            bump(
                STATUS_MANUAL,
                'BROAD_CATEGORY',
                'Товар, похоже, в слишком общей категории. Не создавать новую Category автоматически.',
            )
        else:
            issues.append('CATEGORY_SCHEMA_GAP')
            actions.append(
                'Специализированной категории в справочнике нет. Не считать ошибкой карточки и не создавать Category.'
            )

    key = normalize_article(product.article)
    if key and article_groups and len(article_groups.get(key, [])) > 1:
        siblings = article_groups[key]
        families = {
            part_type_family(part_type_from_text(item.title))
            for item in siblings
        }
        families.discard('')
        if len(families) > 1:
            bump(
                STATUS_CRITICAL,
                'CRITICAL_ARTICLE_CONFLICT',
                'Один article используется у разных типов деталей. Не объединять и не удалять.',
                sev='critical',
            )
        titles = [item.title for item in siblings]
        if has_front_rear_conflict(titles) and len({(item.title or '').casefold() for item in siblings}) > 1:
            bump(
                STATUS_MANUAL,
                'FRONT_REAR_ARTICLE_VARIANTS',
                'Один article встречается как передний и задний — проверить вручную.',
            )

    safe_fixes = _collect_safe_fixes(product)
    if safe_fixes and status == STATUS_OK:
        status = STATUS_AUTO_FIXABLE
        severity = 'info'
        actions.append('Применить безопасную нормализацию текста.')
    if safe_fixes and status == STATUS_AUTO_FIXABLE and not any(
        item.startswith('INTERNAL_TEXT') or item == 'MALFORMED_ARTICLE'
        for item in issues
    ):
        issues.append('WHITESPACE_OR_SAFE_NORMALIZE')

    unique_issues = []
    seen_issues = set()
    for item in issues:
        if item in seen_issues:
            continue
        seen_issues.add(item)
        unique_issues.append(item)

    unique_actions = []
    seen_actions = set()
    for item in actions:
        if item in seen_actions:
            continue
        seen_actions.add(item)
        unique_actions.append(item)

    seller_name = ''
    if getattr(product, 'seller_profile', None) and product.seller_profile:
        seller_name = product.seller_profile.name
    else:
        seller_name = product.seller_name or ''

    return ProductAuditResult(
        product_id=product.pk,
        seller_name=seller_name,
        title=product.title or '',
        article=product.article or '',
        status=status,
        severity=severity,
        issues=unique_issues,
        suggested_actions=unique_actions,
        safe_fixes=safe_fixes,
    )


def _build_article_groups(products: Iterable[Product]) -> dict[str, list[Product]]:
    groups: dict[str, list[Product]] = defaultdict(list)
    for product in products:
        key = normalize_article(product.article)
        if key:
            groups[key].append(product)
    return groups


def _generic_description_keys(products: Iterable[Product]) -> set[str]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for product in products:
        text = (product.description or '').strip().casefold()
        if len(text) < 40:
            continue
        seller = ''
        if getattr(product, 'seller_profile', None) and product.seller_profile:
            seller = product.seller_profile.name.casefold()
        else:
            seller = (product.seller_name or '').casefold()
        counts[(seller, text)] += 1
    return {text for (seller, text), count in counts.items() if count >= 3}


def audit_all_products(queryset=None) -> list[ProductAuditResult]:
    qs = queryset if queryset is not None else Product.objects.all()
    products = list(
        qs.select_related('brand', 'car_model', 'category', 'seller_profile').prefetch_related(
            'selected_brands',
            'selected_models',
        )
    )
    groups = _build_article_groups(products)
    generic = _generic_description_keys(products)
    category_names = _category_name_set(None)
    return [
        audit_product(
            product,
            article_groups=groups,
            generic_descriptions=generic,
            category_names=category_names,
        )
        for product in products
    ]


def summarize_audit(results: list[ProductAuditResult]) -> dict[str, Any]:
    counts = {
        'total': len(results),
        'ok': 0,
        'auto_fixable': 0,
        'manual_review': 0,
        'critical': 0,
    }
    for item in results:
        if item.status == STATUS_OK:
            counts['ok'] += 1
        elif item.status == STATUS_AUTO_FIXABLE:
            counts['auto_fixable'] += 1
        elif item.status == STATUS_MANUAL:
            counts['manual_review'] += 1
        elif item.status == STATUS_CRITICAL:
            counts['critical'] += 1
    counts['safe'] = counts['auto_fixable']
    counts['manual'] = counts['manual_review']
    counts['critical_ids'] = [item.product_id for item in results if item.status == STATUS_CRITICAL]
    counts['manual_ids'] = [item.product_id for item in results if item.status == STATUS_MANUAL]
    counts['internal_text'] = [
        item.product_id for item in results
        if any(issue.startswith('INTERNAL_TEXT') for issue in item.issues)
    ]
    counts['malformed_article'] = [
        item.product_id for item in results
        if 'MALFORMED_ARTICLE' in item.issues
    ]
    counts['article_conflicts'] = [
        item.product_id for item in results
        if 'CRITICAL_ARTICLE_CONFLICT' in item.issues
    ]
    return counts


def write_audit_reports(
    results: list[ProductAuditResult],
    *,
    report_dir: Path,
    stem: str = 'product_card_audit',
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / f'{stem}.csv'
    json_path = report_dir / f'{stem}.json'
    fieldnames = [
        'product_id',
        'seller_name',
        'title',
        'article',
        'status',
        'severity',
        'issues',
        'suggested_actions',
    ]
    with csv_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            writer.writerow(item.as_csv_row())
    payload = {
        'summary': summarize_audit(results),
        'products': [
            {
                **item.as_csv_row(),
                'safe_fixes': item.safe_fixes,
            }
            for item in results
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return csv_path, json_path


def apply_safe_fixes(product: Product, fixes: dict[str, str]) -> list[str]:
    """Write only whitelisted public text fields. Never touch price/status/ownership."""
    changed: list[str] = []
    for name, value in (fixes or {}).items():
        if name not in PUBLIC_TEXT_FIELDS:
            continue
        current = getattr(product, name, '') or ''
        if current == value:
            continue
        setattr(product, name, value)
        changed.append(name)
    if changed:
        product.save(update_fields=changed)
    return changed
