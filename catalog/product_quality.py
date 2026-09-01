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
from catalog.models import Product

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
})
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

SPARK_CATEGORY_NAMES = (
    'Свечи зажигания',
    'Система зажигания',
)

BROAD_CATEGORIES = {
    'электрика': ('spark',),
}


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


def sanitize_oem_text(value: str | None) -> str:
    text = _OEM_LABEL.sub(' ', str(value or ''))
    tokens: list[str] = []
    seen: set[str] = set()
    for token in _OEM_TOKEN.findall(text):
        token = token.strip().strip('.,;:()[]')
        if not token or detect_internal_research_text(token):
            continue
        key = token.casefold()
        if key in seen or key in _OEM_SKIP_TOKENS:
            continue
        seen.add(key)
        tokens.append(token)
    return '\n'.join(tokens)


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

    match = _ARTICLE_WITH_OEM.match(raw) or _ARTICLE_PREFIX.match(raw)
    if match:
        primary = (match.group('article') or '').strip()
        oem_raw = ''
        if 'oem' in match.groupdict() and match.groupdict().get('oem'):
            oem_raw = match.group('oem')
        else:
            oem_raw = raw[len(primary):]
        oem_numbers = sanitize_oem_text(oem_raw).split('\n') if oem_raw else []
        oem_numbers = [item for item in oem_numbers if item and item.casefold() != primary.casefold()]
        confident = bool(primary) and not _OEM_ONLY_LABEL.match(raw)
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
    text = str(value or '').casefold()
    if not text:
        return ''
    for needle, code in PART_TYPE_PATTERNS:
        if needle in text:
            return code
    return ''


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


def _trim_fix(value: str) -> str:
    return _normalize_whitespace(value)


def _collect_safe_fixes(product: Product) -> dict[str, str]:
    fixes: dict[str, str] = {}
    for name in PUBLIC_TEXT_FIELDS:
        current = getattr(product, name, '') or ''
        trimmed = _trim_fix(current)
        result = _field_sanitize(current, name)
        if name == 'article':
            parsed = parse_article_and_oem(current)
            if parsed.confident and parsed.primary and parsed.primary != current.strip():
                fixes['article'] = parsed.primary[:100]
            if parsed.confident and parsed.oem_numbers:
                existing_oem = sanitize_oem_text(product.oem_cross_references or '')
                merged = serialize_plain_list(
                    existing_oem + '\n' + '\n'.join(parsed.oem_numbers)
                )
                if merged and merged != (product.oem_cross_references or '').strip():
                    fixes['oem_cross_references'] = merged
            continue
        if name == 'oem_cross_references':
            cleaned = sanitize_oem_text(current)
            if cleaned and cleaned != current.strip():
                fixes[name] = cleaned
            elif trimmed != current:
                fixes[name] = trimmed
            continue
        if result.safe_to_apply and result.text != current.strip():
            fixes[name] = result.text
        elif trimmed != current:
            fixes[name] = trimmed

    description = product.description or ''
    oem_field = fixes.get('oem_cross_references', product.oem_cross_references or '')
    oem_tokens = {
        item.casefold()
        for item in sanitize_oem_text(oem_field).split('\n')
        if item
    }
    if oem_tokens and description:
        kept_lines = []
        dropped = False
        for line in description.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if _OEM_LABEL.search(stripped):
                line_tokens = {
                    item.casefold()
                    for item in sanitize_oem_text(stripped).split('\n')
                    if item
                }
                if line_tokens and line_tokens <= oem_tokens:
                    dropped = True
                    continue
            kept_lines.append(stripped)
        if dropped:
            new_description = '\n'.join(kept_lines)
            if new_description != description.strip():
                fixes['description'] = new_description
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
    if title_type and description_type and title_type != description_type:
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

    if not (product.article or '').strip():
        bump(STATUS_MANUAL, 'MISSING_ARTICLE', 'Указать артикул.')
    if not product.brand_id:
        bump(STATUS_MANUAL, 'MISSING_BRAND', 'Указать марку.')
    if not product.category_id:
        bump(STATUS_MANUAL, 'MISSING_CATEGORY', 'Указать категорию.')
    if not (product.compatibility or '').strip() and not product.car_model_id:
        bump(STATUS_MANUAL, 'MISSING_COMPATIBILITY', 'Указать применимость.')
    if not (product.description or '').strip():
        bump(STATUS_MANUAL, 'MISSING_DESCRIPTION', 'Добавить описание детали.')

    category_name = (product.category.name if product.category_id else '').casefold()
    if title_type in BROAD_CATEGORIES.get(category_name, ()):
        bump(
            STATUS_MANUAL,
            'BROAD_CATEGORY',
            'Товар, похоже, в слишком общей категории. Не создавать новую Category автоматически.',
        )

    key = normalize_article(product.article)
    if key and article_groups and len(article_groups.get(key, [])) > 1:
        siblings = article_groups[key]
        types = {part_type_from_text(item.title) for item in siblings}
        types.discard('')
        if len(types) > 1:
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
        qs.select_related('brand', 'car_model', 'category', 'seller_profile')
    )
    groups = _build_article_groups(products)
    generic = _generic_description_keys(products)
    return [
        audit_product(
            product,
            article_groups=groups,
            generic_descriptions=generic,
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
