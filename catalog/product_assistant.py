"""Seller product-by-article assistant. Never writes Product rows."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from django.conf import settings
from django.db.models import Q

from catalog.applicability import parse_plain_list
from catalog.article_utils import display_article, normalize_article
from catalog.models import Brand, CarModel, Category, Country, Product
from catalog.product_quality import (
    SPARK_CATEGORY_NAMES,
    detect_internal_research_text,
    normalize_research_notes,
    prefer_public_field,
    research_notes_from_removed,
    sanitize_oem_research,
    sanitize_oem_text,
    sanitize_public_product_text,
    split_public_sentences,
)

logger = logging.getLogger(__name__)

OPENAI_RESPONSES_URL = 'https://api.openai.com/v1/responses'
DEFAULT_PRODUCT_AI_MODEL = 'gpt-5.6-luna'
OPENAI_TIMEOUT = 25
OPENAI_RESEARCH_TIMEOUT = 90
FILTER_CATEGORY_NAME = 'Фильтры'
FILTER_CATEGORY_ALIASES = frozenset({
    'фильтры',
    'воздушные фильтры',
    'воздушный фильтр',
    'масляные фильтры',
    'масляный фильтр',
    'салонные фильтры',
    'салонный фильтр',
    'автомобильные фильтры',
    'автомобильный фильтр',
    'топливные фильтры',
    'топливный фильтр',
    'air filter',
    'air filters',
    'oil filter',
    'cabin filter',
    'cabin filters',
})
_HTTP_URL_RE = re.compile(r'https?://[^\s)>\]]+', re.I)
_COMPAT_BRAND_TOKEN = re.compile(
    r'^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9\-]*',
)
_BRAND_KEY_RE = re.compile(r'[^a-zа-яё0-9]+', re.I)
_BRAND_SPLIT_RE = re.compile(r'\s*(?:[/|,;&+]|\band\b|\bи\b)\s*', re.I)
BRAND_CANONICAL_BY_KEY = {
    'gwm': 'Great Wall',
    'greatwall': 'Great Wall',
    'greatwallmotor': 'Great Wall',
    'greatwallmotors': 'Great Wall',
    'lifan': 'Lifan',
    'лифан': 'Lifan',
}
PROPOSED_DICTIONARY_BRANDS = {
    'lifan': 'Lifan',
}
APPROVAL_FIELDS = (
    'title',
    'brand',
    'category',
    'compatibility',
    'engine_compatibility',
    'oem_cross_references',
    'description',
)
CONFIDENCE_RANK = {
    'confirmed': 0,
    'likely': 1,
    'needs_verification': 2,
}
VALID_CONFIDENCE = frozenset(CONFIDENCE_RANK)


@dataclass
class OpenAIEnrichment:
    title: str = ''
    category: str = ''
    brand: str = ''
    models: list[str] = field(default_factory=list)
    compatibility: str = ''
    engine_compatibility: str = ''
    oem_cross_references: str = ''
    description: str = ''
    research_notes: list[dict[str, str]] = field(default_factory=list)
    confidence: str = 'needs_verification'
    sources: list[dict[str, str]] = field(default_factory=list)
    web_search_used: bool = False


def _most_common(values: list[Any]) -> Any | None:
    cleaned = [value for value in values if value not in (None, '', [])]
    if not cleaned:
        return None
    counts = Counter(cleaned)
    return counts.most_common(1)[0][0]


def _most_common_text(values: list[str]) -> str:
    cleaned = [' '.join(str(value or '').split()) for value in values]
    cleaned = [value for value in cleaned if value]
    if not cleaned:
        return ''
    counts = Counter(cleaned)
    return counts.most_common(1)[0][0]


def _merge_plain_lists(values: list[str]) -> str:
    items: list[str] = []
    seen: set[str] = set()
    for raw in values:
        for item in parse_plain_list(raw):
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return '\n'.join(items)


def find_products_by_article(
    article: str,
    *,
    exclude_product_ids: list[int] | tuple[int, ...] | set[int] | None = None,
    exclude_product_id: int | None = None,
) -> list[Product]:
    raw = display_article(article)
    key = normalize_article(raw)
    if not key:
        return []

    excluded: set[int] = set()
    for item in exclude_product_ids or []:
        try:
            excluded.add(int(item))
        except (TypeError, ValueError):
            continue
    if exclude_product_id:
        try:
            excluded.add(int(exclude_product_id))
        except (TypeError, ValueError):
            pass

    qs = Product.objects.select_related(
        'brand',
        'brand__country',
        'car_model',
        'category',
    ).prefetch_related('selected_models', 'selected_brands')
    if excluded:
        qs = qs.exclude(pk__in=excluded)

    matches = list(
        qs.filter(Q(article__iexact=raw) | Q(article__iexact=key))
    )
    matched_ids = {product.pk for product in matches}

    extras = []
    if len(key) >= 4:
        extras = list(qs.exclude(pk__in=matched_ids).filter(article__icontains=raw[:4])[:80])
        if len(extras) < 5:
            extras.extend(
                list(
                    qs.exclude(pk__in=matched_ids).filter(article__icontains=key[:4])[:80]
                )
            )

    for product in extras:
        if product.pk in matched_ids or product.pk in excluded:
            continue
        if normalize_article(product.article) == key:
            matches.append(product)
            matched_ids.add(product.pk)
    return matches


def _is_filter_category_alias(name: str) -> bool:
    text = ' '.join((name or '').split()).casefold()
    if not text:
        return False
    if text in FILTER_CATEGORY_ALIASES:
        return True
    return bool(
        re.search(
            r'(воздушн|маслян|салонн|автомобильн|топливн).{0,20}фильтр'
            r'|фильтр.{0,20}(воздушн|маслян|салонн|автомобильн|топливн)',
            text,
            re.I,
        )
    )


def _match_category(name: str, *, title: str = '') -> Category | None:
    text = (name or '').strip()
    blob = f'{text} {title or ""}'
    if re.search(r'свеч|spark\s*plug|зажиган', blob, re.I):
        for preferred in SPARK_CATEGORY_NAMES:
            found = Category.objects.filter(name__iexact=preferred).first()
            if found:
                return found
    if _is_filter_category_alias(text) or _is_filter_category_alias(title):
        found = Category.objects.filter(name__iexact=FILTER_CATEGORY_NAME).first()
        if found:
            return found
    if not text:
        return None
    exact = list(Category.objects.filter(name__iexact=text)[:2])
    if len(exact) == 1:
        return exact[0]
    if exact:
        return exact[0]
    contains = list(Category.objects.filter(name__icontains=text)[:3])
    if len(contains) == 1:
        return contains[0]
    return None


def _brand_key(name: str) -> str:
    text = str(name or '').casefold().replace('ё', 'е')
    return _BRAND_KEY_RE.sub('', text)


def _canonical_brand_label(name: str) -> str:
    raw = ' '.join(str(name or '').split())
    if not raw:
        return ''
    mapped = BRAND_CANONICAL_BY_KEY.get(_brand_key(raw))
    return mapped or raw


def _brand_equivalence_keys(name: str) -> set[str]:
    raw = ' '.join(str(name or '').split())
    if not raw:
        return set()
    keys = {_brand_key(raw)}
    canonical = _canonical_brand_label(raw)
    if canonical:
        keys.add(_brand_key(canonical))
    target = _brand_key(canonical or raw)
    for alias_key, canon in BRAND_CANONICAL_BY_KEY.items():
        if _brand_key(canon) == target or alias_key == target:
            keys.add(alias_key)
            keys.add(_brand_key(canon))
    return {item for item in keys if item}


def _proposed_dictionary_brand(name: str) -> str:
    key = _brand_key(_canonical_brand_label(name) or name)
    return PROPOSED_DICTIONARY_BRANDS.get(key) or ''


def _brand_tokens(name: str) -> list[str]:
    text = ' '.join(str(name or '').split())
    if not text:
        return []
    parts = [
        item.strip()
        for item in _BRAND_SPLIT_RE.split(text)
        if item and item.strip()
    ]
    tokens: list[str] = []
    seen: set[str] = set()
    for item in parts:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(item)
    return tokens or ([text] if text else [])


def _brand_labels(brand: Brand) -> list[str]:
    labels = [' '.join(str(brand.name or '').split())]
    canonical = _canonical_brand_label(brand.name)
    if canonical and canonical not in labels:
        labels.append(canonical)
    target = _brand_key(canonical or brand.name)
    for alias_key, canon in BRAND_CANONICAL_BY_KEY.items():
        if _brand_key(canon) != target:
            continue
        if canon not in labels:
            labels.append(canon)
        compact = alias_key.upper() if alias_key.isascii() else alias_key
        if compact not in labels:
            labels.append(compact)
        if alias_key == 'gwm' and 'GWM' not in labels:
            labels.append('GWM')
    return [item for item in labels if item]


def _brand_word_pattern(label: str) -> re.Pattern[str]:
    return re.compile(
        rf'(?<![\w]){re.escape(label)}(?![\w])',
        re.I,
    )


def _first_existing_brand_in_text(*texts: str) -> Brand | None:
    blob = ' '.join(' '.join(str(item or '').split()) for item in texts if item)
    if not blob:
        return None
    best: Brand | None = None
    best_pos = -1
    best_len = -1
    for brand in Brand.objects.select_related('country').order_by('name', 'id'):
        for label in _brand_labels(brand):
            match = _brand_word_pattern(label).search(blob)
            if not match:
                continue
            pos = match.start()
            label_len = len(label)
            if (
                best is None
                or pos < best_pos
                or (pos == best_pos and label_len > best_len)
            ):
                best = brand
                best_pos = pos
                best_len = label_len
            break
    return best


def _match_brand_single(name: str) -> Brand | None:
    text = (name or '').strip()
    if not text:
        return None
    wanted = _brand_equivalence_keys(text)
    canonical = _canonical_brand_label(text)
    names = []
    for item in (canonical, text):
        label = ' '.join(str(item or '').split())
        if label and label not in names:
            names.append(label)
    for label in names:
        qs = Brand.objects.select_related('country').filter(name__iexact=label).order_by(
            'country__name',
            'name',
            'id',
        )
        found = list(qs[:5])
        if found:
            return found[0]
    matches = []
    for brand in Brand.objects.select_related('country').order_by('country__name', 'name', 'id'):
        if _brand_equivalence_keys(brand.name) & wanted:
            matches.append(brand)
            if len(matches) > 2:
                break
    if len(matches) == 1:
        return matches[0]
    if matches:
        return matches[0]
    contains = list(
        Brand.objects.select_related('country').filter(name__icontains=canonical or text).order_by(
            'name',
            'id',
        )[:3]
    )
    if len(contains) == 1:
        return contains[0]
    return None


def _match_brand(name: str) -> Brand | None:
    text = (name or '').strip()
    if not text:
        return None
    found = _match_brand_single(text)
    if found is not None:
        return found
    tokens = _brand_tokens(text)
    if len(tokens) <= 1:
        return None
    for token in tokens:
        found = _match_brand_single(token)
        if found is not None:
            return found
    return None


def _resolve_primary_brand(
    name: str,
    *,
    title: str = '',
    compatibility: str = '',
) -> Brand | None:
    found = _match_brand(name)
    if found is not None:
        return found
    return _first_existing_brand_in_text(name, title, compatibility)


def _split_model_brand_prefix(name: str) -> tuple[Brand | None, str]:
    text = ' '.join(str(name or '').split())
    if not text:
        return None, ''
    brands = list(Brand.objects.select_related('country').order_by('name', 'id'))
    brands.sort(key=lambda item: len(item.name or ''), reverse=True)
    folded = text.casefold()
    for brand in brands:
        for label in _brand_labels(brand):
            prefix = label.casefold()
            if not prefix:
                continue
            if folded == prefix:
                return brand, ''
            if folded.startswith(prefix + ' '):
                rest = text[len(label):].strip()
                if rest:
                    return brand, rest
    return None, text


def _match_country(name: str) -> Country | None:
    text = (name or '').strip()
    if not text:
        return None
    return Country.objects.filter(name__iexact=text).order_by('id').first()


def _match_car_model(name: str, brand: Brand | None) -> CarModel | None:
    text = ' '.join(str(name or '').split())
    if not text:
        return None
    prefix_brand, remainder = _split_model_brand_prefix(text)
    if brand is not None and prefix_brand is not None and prefix_brand.pk != brand.pk:
        return None
    labels: list[str] = []
    for item in (remainder, text):
        label = ' '.join(str(item or '').split())
        if label and label not in labels:
            labels.append(label)
    qs = CarModel.objects.select_related('brand', 'brand__country')
    scoped_brand = brand if brand is not None else prefix_brand
    if scoped_brand is not None:
        for label in labels:
            found = qs.filter(brand=scoped_brand, name__iexact=label).order_by('id').first()
            if found:
                return found
        for label in labels:
            contains = list(
                qs.filter(brand=scoped_brand, name__icontains=label).order_by('id')[:2]
            )
            if len(contains) == 1:
                return contains[0]
        return None
    for label in labels:
        items = list(qs.filter(name__iexact=label).order_by('id')[:2])
        if len(items) == 1:
            return items[0]
        if len(items) > 1:
            return None
    return None


def _conservative_confidence(*values: str) -> str:
    chosen = 'confirmed'
    chosen_rank = CONFIDENCE_RANK[chosen]
    any_valid = False
    for value in values:
        key = (value or '').strip()
        if key not in VALID_CONFIDENCE:
            continue
        any_valid = True
        rank = CONFIDENCE_RANK[key]
        if rank > chosen_rank:
            chosen = key
            chosen_rank = rank
    return chosen if any_valid else 'needs_verification'


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
    start = raw.find('{')
    end = raw.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('no json object')
    payload = json.loads(raw[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError('json is not an object')
    return payload


def _add_http_source(
    sources: list[dict[str, str]],
    seen: set[str],
    url: str,
    title: str = '',
) -> None:
    raw = str(url or '').strip()
    if not raw:
        return
    if raw.startswith(('http://', 'https://')):
        href = raw
    else:
        match = _HTTP_URL_RE.search(raw)
        if not match:
            return
        href = match.group(0)
    if href in seen:
        return
    seen.add(href)
    label = str(title or '').strip() or href
    sources.append({'title': label, 'url': href})


def _collect_action_urls(action: dict[str, Any], sources: list[dict[str, str]], seen: set[str]) -> None:
    if not isinstance(action, dict):
        return
    action_type = str(action.get('type') or '').strip()
    for src in action.get('sources') or []:
        if isinstance(src, dict):
            _add_http_source(
                sources,
                seen,
                str(src.get('url') or src.get('href') or ''),
                str(src.get('title') or src.get('text') or ''),
            )
        elif isinstance(src, str):
            _add_http_source(sources, seen, src)
    if action_type in {'open_page', 'find_in_page'} or action.get('url'):
        _add_http_source(
            sources,
            seen,
            str(action.get('url') or action.get('href') or ''),
            str(action.get('title') or ''),
        )
    for key in ('open_page', 'find_in_page'):
        nested = action.get(key)
        if isinstance(nested, dict):
            _add_http_source(
                sources,
                seen,
                str(nested.get('url') or nested.get('href') or ''),
                str(nested.get('title') or ''),
            )
        elif isinstance(nested, str):
            _add_http_source(sources, seen, nested)


def _walk_openai_text(payload: dict[str, Any]) -> tuple[str, list[dict[str, str]], bool]:
    if payload.get('output_text'):
        text = str(payload.get('output_text') or '')
    else:
        chunks: list[str] = []
        for item in payload.get('output') or []:
            if not isinstance(item, dict):
                continue
            for content in item.get('content') or []:
                if not isinstance(content, dict):
                    continue
                piece = content.get('text') or content.get('output_text')
                if piece:
                    chunks.append(str(piece))
        text = '\n'.join(chunks)

    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    web_search_used = False
    for item in payload.get('output') or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get('type') or '')
        if item_type == 'web_search_call' or item_type.startswith('web_search'):
            web_search_used = True
            action = item.get('action') if isinstance(item.get('action'), dict) else {}
            _collect_action_urls(action, sources, seen)
            for src in item.get('sources') or []:
                if isinstance(src, dict):
                    _add_http_source(
                        sources,
                        seen,
                        str(src.get('url') or src.get('href') or ''),
                        str(src.get('title') or ''),
                    )
        for content in item.get('content') or []:
            if not isinstance(content, dict):
                continue
            for annotation in content.get('annotations') or []:
                if not isinstance(annotation, dict):
                    continue
                url = str(annotation.get('url') or annotation.get('href') or '').strip()
                if not url:
                    continue
                _add_http_source(
                    sources,
                    seen,
                    url,
                    str(annotation.get('title') or annotation.get('text') or ''),
                )
    return text, sources, web_search_used


def call_openai_product_lookup(
    article: str,
    local_fields: dict[str, Any],
    *,
    urlopen: Callable[..., Any] | None = None,
    research_mode: bool = False,
) -> OpenAIEnrichment | None:
    api_key = (getattr(settings, 'OPENAI_API_KEY', '') or '').strip()
    if not api_key:
        return None

    model = (getattr(settings, 'PRODUCT_AI_MODEL', '') or '').strip() or DEFAULT_PRODUCT_AI_MODEL
    prompt = (
        'Ты формируешь ПУБЛИЧНЫЕ поля карточки товара ZPT.KZ. '
        'По артикулу найди данные. Не выдумывай фото. Не публикуй товар.\n'
        'Никогда не включай в public fields: reasoning, историю поиска, '
        'rejected candidates, поставщиков, источники, названия сайтов, '
        'информацию «нет в справочнике ZPT», сведения о том, какие варианты '
        'ты отверг, оценку качества источника, FitInPart, Gemini/ChatGPT, '
        'имена файлов, «подтверждено каталогами».\n'
        'compatibility — только марка/модель/поколение/годы/двигатель. '
        'Пример: «Changan CS75 Plus, UNI-K — 2.0T».\n'
        'engine_compatibility — только двигатели, одно значение на строку.\n'
        'oem_cross_references — только номера, без слов OEM/кросс/аналог.\n'
        'description — пользовательский текст о товаре: назначение, комплект, '
        'особенности, краткая применимость, рекомендация проверить VIN.\n'
        'title — короткое товарное название без истории поиска.\n'
        'Категория — только существующее имя из данных ZPT, без создания новых. '
        'Для свечи зажигания предпочитай «Свечи зажигания» или «Система зажигания», '
        'а не общую «Электрику», если такая категория есть.\n'
        'Для воздушных, масляных, салонных и автомобильных фильтров используй '
        'категорию «Фильтры».\n'
        'Все сомнения и дополнительные варианты клади ТОЛЬКО в research_notes.\n'
        'raw_historical_context — грязные старые тексты, не копируй их в public fields.\n'
        'Верни ТОЛЬКО JSON без markdown:\n'
        '{'
        '"title":"","category":"","brand":"","models":[],'
        '"compatibility":"","engine_compatibility":"",'
        '"oem_cross_references":"","description":"",'
        '"research_notes":[{"text":"","severity":"info"}],'
        '"confidence":"confirmed|likely|needs_verification"'
        '}\n'
        f'Артикул: {article}\n'
        f'Контекст: {json.dumps(local_fields, ensure_ascii=False)}\n'
        'Если данных мало — confidence=needs_verification.'
    )
    if research_mode:
        prompt += (
            '\nИсследовательский режим: обязательно используй web_search. '
            'Новые факты (марка, применимость, двигатели, OEM/кроссы) указывай '
            'только если они подтверждены найденными страницами.\n'
            f'Запрошенный артикул «{article}» — основной номер детали. '
            'Не называй другой кросс OEM вместо него. '
            'В oem_cross_references — только подтверждённые аналоги, не сам артикул.\n'
            'Не выдумывай CarModel. Если модели нет в справочнике, оставь '
            'текстовую применимость и запиши это в research_notes.\n'
            'Если марка и применимость противоречат (например Lexus и LIFAN), '
            'confidence=needs_verification.'
        )
    payload_body: dict[str, Any] = {
        'model': model,
        'tools': [{'type': 'web_search'}],
        'input': prompt,
    }
    if research_mode:
        payload_body['tool_choice'] = 'required'
        payload_body['include'] = ['web_search_call.action.sources']
    body = json.dumps(payload_body).encode('utf-8')
    http_request = urllib_request.Request(
        OPENAI_RESPONSES_URL,
        data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
    )
    open_url = urlopen or (
        lambda req, timeout: urllib_request.build_opener(
            urllib_request.ProxyHandler({})
        ).open(req, timeout=timeout)
    )
    timeout = OPENAI_RESEARCH_TIMEOUT if research_mode else OPENAI_TIMEOUT
    try:
        with open_url(http_request, timeout=timeout) as response:
            raw = response.read()
            status = int(getattr(response, 'status', 200) or 200)
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        logger.warning('OpenAI product assistant unavailable: %s', exc)
        return None
    if status >= 400:
        logger.warning('OpenAI product assistant HTTP %s', status)
        return None
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning('OpenAI product assistant returned invalid JSON')
        return None
    if not isinstance(payload, dict):
        return None
    text, sources, web_search_used = _walk_openai_text(payload)
    try:
        data = _extract_json_object(text)
    except (ValueError, json.JSONDecodeError):
        logger.warning('OpenAI product assistant JSON payload missing')
        return None

    models_raw = data.get('models') or []
    if isinstance(models_raw, str):
        models = [item for item in parse_plain_list(models_raw)]
    elif isinstance(models_raw, list):
        models = [str(item).strip() for item in models_raw if str(item).strip()]
    else:
        models = []

    confidence = str(data.get('confidence') or 'needs_verification').strip()
    if confidence not in VALID_CONFIDENCE:
        confidence = 'needs_verification'
    if research_mode and not web_search_used:
        confidence = 'needs_verification'

    enrichment = OpenAIEnrichment(
        title=str(data.get('title') or '').strip(),
        category=str(data.get('category') or '').strip(),
        brand=str(data.get('brand') or '').strip(),
        models=models,
        compatibility=str(data.get('compatibility') or '').strip(),
        engine_compatibility=str(data.get('engine_compatibility') or '').strip(),
        oem_cross_references=str(data.get('oem_cross_references') or '').strip(),
        description=str(data.get('description') or '').strip(),
        research_notes=normalize_research_notes(data.get('research_notes')),
        confidence=confidence,
        sources=sources,
        web_search_used=web_search_used,
    )
    return _sanitize_enrichment(enrichment, queried_article=article)


def _notes_from_dirty_text(value: str, *, field: str) -> list[dict[str, str]]:
    if not value or not detect_internal_research_text(value):
        return []
    removed = [
        sentence
        for sentence in split_public_sentences(value)
        if detect_internal_research_text(sentence)
    ]
    if not removed:
        removed = [value]
    return research_notes_from_removed(removed, severity='warning')


def _sanitize_enrichment(
    enrichment: OpenAIEnrichment,
    *,
    queried_article: str = '',
) -> OpenAIEnrichment:
    notes = list(enrichment.research_notes)
    mapping = (
        ('title', 'title'),
        ('compatibility', 'compatibility'),
        ('engine_compatibility', 'engine_compatibility'),
        ('oem_cross_references', 'oem_cross_references'),
        ('description', 'description'),
    )
    for attr, field_name in mapping:
        raw = getattr(enrichment, attr) or ''
        notes.extend(_notes_from_dirty_text(raw, field=field_name))
        cleaned = sanitize_public_product_text(raw, field=field_name, mode='preview')
        if field_name == 'oem_cross_references':
            cleaned, rejected = sanitize_oem_research(raw, queried_article=queried_article)
            if rejected:
                notes.append({
                    'text': f'oem_cross_references: фрагментированный/невалидный список отклонён: {rejected}',
                    'severity': 'warning',
                })
        setattr(enrichment, attr, cleaned)
    enrichment.research_notes = normalize_research_notes(notes)
    return enrichment


def _aggregate_local(products: list[Product]) -> dict[str, Any]:
    titles = [product.title for product in products]
    descriptions = [product.description for product in products]
    compat = [product.compatibility for product in products]
    engines = [product.engine_compatibility for product in products]
    oems = [product.oem_cross_references for product in products]
    brands = [product.brand for product in products if product.brand_id]
    models = [product.car_model for product in products if product.car_model_id]
    categories = [product.category for product in products if product.category_id]

    extra_models: list[CarModel] = []
    for product in products:
        extra_models.extend(list(product.selected_models.all()))
        if product.car_model_id:
            extra_models.append(product.car_model)

    brand = _most_common(brands)
    car_model = _most_common(models)
    category = _most_common(categories)
    country = brand.country if brand is not None else None

    selected = []
    seen_model_ids: set[int] = set()
    for model in extra_models:
        if model is None or model.pk in seen_model_ids:
            continue
        if car_model is not None and model.pk == car_model.pk:
            continue
        seen_model_ids.add(model.pk)
        selected.append(model)

    brand_ids = {item.pk for item in brands if item is not None}
    category_ids = {item.pk for item in categories if item is not None}
    conflicts = len(brand_ids) > 1 or len(category_ids) > 1

    return {
        'title': _most_common_text(titles),
        'description': _most_common_text(descriptions),
        'compatibility': _merge_plain_lists(compat) or _most_common_text(compat),
        'engine_compatibility': _merge_plain_lists(engines),
        'oem_cross_references': _merge_plain_lists(oems),
        'brand': brand,
        'car_model': car_model,
        'category': category,
        'country': country,
        'selected_models': selected,
        'conflicts': conflicts,
        'complete': bool(
            _most_common_text(titles)
            and (brand is not None or category is not None)
        ),
    }


def _local_fields_for_ai(local_preview: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    raw_history: dict[str, str] = {}
    text_fields = {
        'title',
        'compatibility',
        'engine_compatibility',
        'oem_cross_references',
        'description',
    }
    for key, value in local_preview.items():
        if key not in text_fields:
            public[key] = value
            continue
        text = str(value or '')
        if detect_internal_research_text(text):
            raw_history[key] = text
            public[key] = sanitize_public_product_text(text, field=key, mode='preview')
        else:
            public[key] = text
    if raw_history:
        public['raw_historical_context'] = raw_history
    return public


def _invoke_openai_caller(
    caller: Callable[..., OpenAIEnrichment | None],
    article: str,
    local_fields: dict[str, Any],
    **kwargs,
) -> OpenAIEnrichment | None:
    try:
        return caller(article, local_fields, **kwargs)
    except TypeError:
        return caller(article, local_fields)


def suggest_product_by_article(
    article: str,
    *,
    openai_caller: Callable[..., OpenAIEnrichment | None] | None = None,
    exclude_product_ids: list[int] | tuple[int, ...] | set[int] | None = None,
    exclude_product_id: int | None = None,
    research_mode: bool = False,
) -> dict[str, Any]:
    raw = display_article(article)
    key = normalize_article(raw)
    empty = {
        'ok': False,
        'error': 'Укажите артикул.',
        'article': '',
        'normalized_article': '',
        'ai_used': False,
        'match_count': 0,
        'confidence': 'needs_verification',
        'fields': {},
        'research_notes': [],
        'unmatched': [],
        'sources': [],
        'web_search_used': False,
        'source_count': 0,
    }
    if not key:
        return empty

    products = find_products_by_article(
        raw,
        exclude_product_ids=exclude_product_ids,
        exclude_product_id=exclude_product_id,
    )
    local = _aggregate_local(products) if products else {
        'title': '',
        'description': '',
        'compatibility': '',
        'engine_compatibility': '',
        'oem_cross_references': '',
        'brand': None,
        'car_model': None,
        'category': None,
        'country': None,
        'selected_models': [],
        'conflicts': False,
        'complete': False,
    }

    local_preview = {
        'title': local.get('title') or '',
        'category': local['category'].name if local.get('category') else '',
        'brand': local['brand'].name if local.get('brand') else '',
        'models': (
            [local['car_model'].name]
            if local.get('car_model')
            else []
        ) + [model.name for model in local.get('selected_models') or []],
        'compatibility': local.get('compatibility') or '',
        'engine_compatibility': local.get('engine_compatibility') or '',
        'oem_cross_references': local.get('oem_cross_references') or '',
        'description': local.get('description') or '',
    }
    ai_context = _local_fields_for_ai(local_preview)

    enrichment = None
    ai_error = ''
    caller = openai_caller or call_openai_product_lookup
    try:
        enrichment = _invoke_openai_caller(
            caller,
            raw,
            ai_context,
            research_mode=research_mode,
        )
    except Exception as exc:  # noqa: BLE001 — assistant must not break the form
        logger.warning('Product AI enrichment failed: %s', exc)
        enrichment = None
        ai_error = 'AI-проверка недоступна, показаны данные ZPT.KZ.'

    unmatched: list[str] = []
    sources: list[dict[str, str]] = []
    research_notes: list[dict[str, str]] = []

    for field_name, raw_value in (
        ('title', local.get('title') or ''),
        ('compatibility', local.get('compatibility') or ''),
        ('engine_compatibility', local.get('engine_compatibility') or ''),
        ('oem_cross_references', local.get('oem_cross_references') or ''),
        ('description', local.get('description') or ''),
    ):
        research_notes.extend(_notes_from_dirty_text(raw_value, field=field_name))

    title = prefer_public_field(local.get('title') or '', '', field='title')
    compatibility = prefer_public_field(
        local.get('compatibility') or '',
        '',
        field='compatibility',
    )
    engines = prefer_public_field(
        local.get('engine_compatibility') or '',
        '',
        field='engine_compatibility',
    )
    oems = prefer_public_field(
        local.get('oem_cross_references') or '',
        '',
        field='oem_cross_references',
    )
    description = prefer_public_field(
        local.get('description') or '',
        '',
        field='description',
    )
    brand = local.get('brand')
    car_model = local.get('car_model')
    category = local.get('category')
    country = local.get('country')
    selected_models = list(local.get('selected_models') or [])

    if enrichment is not None:
        enrichment = _sanitize_enrichment(enrichment, queried_article=raw)
        sources = list(enrichment.sources)
        research_notes.extend(enrichment.research_notes)
        title = prefer_public_field(local.get('title') or '', enrichment.title, field='title')
        compatibility = prefer_public_field(
            local.get('compatibility') or '',
            enrichment.compatibility,
            field='compatibility',
        )
        engines = prefer_public_field(
            local.get('engine_compatibility') or '',
            '\n'.join(parse_plain_list(enrichment.engine_compatibility)),
            field='engine_compatibility',
        )
        oems = prefer_public_field(
            local.get('oem_cross_references') or '',
            sanitize_oem_text(enrichment.oem_cross_references, queried_article=raw),
            field='oem_cross_references',
        )
        description = prefer_public_field(
            local.get('description') or '',
            enrichment.description,
            field='description',
        )

        if category is None and enrichment.category:
            category = _match_category(enrichment.category, title=title or enrichment.title)
            if category is None:
                unmatched.append(
                    f'Категория «{enrichment.category}» не найдена в справочнике. '
                    'Выберите ближайшую вручную.'
                )
        if brand is None:
            brand = _resolve_primary_brand(
                enrichment.brand,
                title=title or enrichment.title,
                compatibility=compatibility or enrichment.compatibility,
            )
            if brand is None and enrichment.brand:
                unmatched.append(
                    f'Марка «{enrichment.brand}» не найдена в справочнике. '
                    'Выберите марку вручную.'
                )
            elif brand is not None:
                country = brand.country
        missing_catalog_models = False
        for model_name in enrichment.models:
            raw_model = ' '.join(str(model_name or '').split())
            if not raw_model:
                continue
            prefix_brand, remainder = _split_model_brand_prefix(raw_model)
            if (
                brand is not None
                and prefix_brand is not None
                and prefix_brand.pk != brand.pk
            ):
                unmatched.append(
                    f'Модель «{raw_model}» относится к марке «{prefix_brand.name}», '
                    f'а основная марка — «{brand.name}». '
                    'Она сохранена в поле «Подходит для» и не добавлена в selected_models.'
                )
                missing_catalog_models = True
                continue
            matched_model = _match_car_model(raw_model, brand)
            if matched_model is None:
                unmatched.append(
                    f'Модель «{raw_model}» не найдена в справочнике CarModel. '
                    'Текстовая применимость сохранена, строка CarModel не создана.'
                )
                missing_catalog_models = True
                continue
            if brand is None:
                brand = matched_model.brand
                country = brand.country if brand is not None else country
            if brand is not None and matched_model.brand_id != brand.pk:
                unmatched.append(
                    f'Модель «{raw_model}» относится к марке «{matched_model.brand.name}», '
                    f'а основная марка — «{brand.name}». '
                    'Она сохранена в поле «Подходит для» и не добавлена в selected_models.'
                )
                missing_catalog_models = True
                continue
            if car_model is None:
                car_model = matched_model
            elif matched_model.pk != car_model.pk:
                if all(item.pk != matched_model.pk for item in selected_models):
                    selected_models.append(matched_model)
        if brand is not None:
            selected_models = [
                item for item in selected_models
                if item is not None and item.brand_id == brand.pk
            ]
            if car_model is not None and car_model.brand_id != brand.pk:
                car_model = None
        if missing_catalog_models:
            research_notes.append({
                'text': (
                    'Модели, которых нет в справочнике, сохранены в поле «Подходит для» '
                    'и не мешают сохранению.'
                ),
                'severity': 'info',
            })

    local_dirty = any(
        detect_internal_research_text(local.get(name) or '')
        for name in (
            'title',
            'compatibility',
            'engine_compatibility',
            'oem_cross_references',
            'description',
        )
    )
    if products:
        if local.get('conflicts') or local_dirty:
            local_confidence = 'likely'
        elif local.get('complete'):
            local_confidence = 'confirmed'
        else:
            local_confidence = 'likely'
    else:
        local_confidence = 'needs_verification'

    ai_confidence = enrichment.confidence if enrichment is not None else ''
    confidence = _conservative_confidence(local_confidence, ai_confidence)
    if not products and confidence == 'confirmed':
        confidence = 'likely'
    if local_dirty and enrichment is not None:
        confidence = _conservative_confidence(confidence, 'likely')
    web_search_used = bool(enrichment.web_search_used) if enrichment is not None else False
    source_count = len({
        str(item.get('url') or '').strip()
        for item in sources
        if isinstance(item, dict) and str(item.get('url') or '').strip()
    })
    if research_mode and (not web_search_used or source_count == 0):
        confidence = 'needs_verification'

    fields = {
        'title': title,
        'category_id': category.pk if category is not None else None,
        'category_name': category.name if category is not None else '',
        'country_id': country.pk if country is not None else None,
        'country_name': country.name if country is not None else '',
        'brand_id': brand.pk if brand is not None else None,
        'brand_name': brand.name if brand is not None else '',
        'car_model_id': car_model.pk if car_model is not None else None,
        'car_model_name': car_model.name if car_model is not None else '',
        'selected_models': [
            {'id': model.pk, 'name': model.name}
            for model in selected_models
        ],
        'compatibility': compatibility,
        'engine_compatibility': engines,
        'oem_cross_references': oems,
        'description': description,
    }

    found_anything = bool(
        products
        or title
        or fields['category_id']
        or fields['brand_id']
        or compatibility
        or engines
        or oems
        or description
        or unmatched
        or research_notes
    )
    message = ''
    if not found_anything:
        message = 'По этому артикулу ничего не найдено. Заполните карточку вручную.'

    return {
        'ok': True,
        'error': '',
        'message': message,
        'article': raw,
        'normalized_article': key,
        'ai_used': enrichment is not None,
        'ai_error': ai_error,
        'match_count': len(products),
        'confidence': confidence,
        'fields': fields,
        'research_notes': normalize_research_notes(research_notes),
        'unmatched': unmatched,
        'sources': sources,
        'web_search_used': web_search_used,
        'source_count': source_count,
    }


_PREVIEW_TEXT_FIELDS = (
    'title',
    'compatibility',
    'engine_compatibility',
    'oem_cross_references',
    'description',
)

_UNRESOLVED_REASONS = {
    'title': 'Название не подтверждено по артикулу.',
    'brand': 'Марка не сопоставлена с существующим справочником.',
    'category': 'Категория не сопоставлена с существующим справочником.',
    'compatibility': 'Применимость не подтверждена. Не выдумывать автомобили.',
    'engine_compatibility': 'Двигатели не подтверждены источниками.',
    'oem_cross_references': 'OEM/кросс-номера не найдены.',
    'description': 'Публичное описание не сформировано без служебного текста.',
}


def _preview_unresolved(
    fields: dict[str, Any],
    *,
    unmatched: list[str],
    confidence: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    mapping = (
        ('title', fields.get('title')),
        ('brand', fields.get('brand_name')),
        ('category', fields.get('category_name')),
        ('compatibility', fields.get('compatibility')),
        ('engine_compatibility', fields.get('engine_compatibility')),
        ('oem_cross_references', fields.get('oem_cross_references')),
        ('description', fields.get('description')),
    )
    for name, value in mapping:
        if str(value or '').strip():
            continue
        reason = _UNRESOLVED_REASONS[name]
        if confidence == 'needs_verification':
            reason += ' Источник недостаточный или требует проверки.'
        rows.append({'field': name, 'reason': reason})
    for item in unmatched:
        text = str(item or '').strip()
        if not text:
            continue
        field_name = 'unmatched'
        lowered = text.casefold()
        if 'категор' in lowered:
            field_name = 'category'
        elif 'марка' in lowered:
            field_name = 'brand'
        elif 'модель' in lowered or 'carmodel' in lowered:
            field_name = 'car_model'
        rows.append({'field': field_name, 'reason': text})
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in rows:
        key = (item['field'], item['reason'])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


_PREVIEW_FITMENT_FIELDS = (
    'compatibility',
    'engine_compatibility',
    'oem_cross_references',
)
_PREVIEW_VERIFY_FIELDS = (
    ('title', 'название'),
    ('compatibility', 'применимость'),
    ('engine_compatibility', 'двигатели'),
    ('oem_cross_references', 'OEM'),
)
ISSUE_BRAND_COMPATIBILITY_CONFLICT = 'BRAND_COMPATIBILITY_CONFLICT'


def _preview_clean_text(value: str, *, field: str, queried_article: str = '') -> str:
    cleaned = sanitize_public_product_text(value or '', field=field, mode='preview')
    if field == 'oem_cross_references':
        cleaned = sanitize_oem_text(cleaned or value or '', queried_article=queried_article)
    return cleaned


def _preview_current_clean(product: Product, field: str) -> str:
    return prefer_public_field(getattr(product, field, '') or '', '', field=field)


def _dedupe_sources(items) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get('url') or '').strip()
        title = str(item.get('title') or url).strip()
        if not url and not title:
            continue
        key = url or title
        if key in seen:
            continue
        seen.add(key)
        sources.append({'title': title or url, 'url': url})
    return sources


def _article_tied_to_value(article: str, *, value: str = '', sources=None) -> bool:
    key = normalize_article(article)
    if len(key) < 5:
        return False
    parts = [value or '']
    for item in sources or []:
        if isinstance(item, dict):
            parts.append(str(item.get('url') or ''))
            parts.append(str(item.get('title') or ''))
        else:
            parts.append(str(item))
    return key in normalize_article(' '.join(parts))


def brand_compatibility_conflict(brand_name: str, compatibility: str) -> bool:
    brand = ' '.join(str(brand_name or '').split())
    text = ' '.join(str(compatibility or '').split())
    if not brand or not text:
        return False
    brand_keys = _brand_equivalence_keys(brand)
    folded = text.casefold()
    for key in brand_keys:
        if key and key in _brand_key(text):
            return False
    labels = {brand, _canonical_brand_label(brand)}
    for alias_key, canon in BRAND_CANONICAL_BY_KEY.items():
        if alias_key in brand_keys or _brand_key(canon) in brand_keys:
            labels.add(canon)
            if alias_key == 'gwm':
                labels.add('GWM')
    for label in labels:
        token = ' '.join(str(label or '').split())
        if not token:
            continue
        if re.search(rf'(?<!\w){re.escape(token)}(?!\w)', text, re.I):
            return False
    lead = _COMPAT_BRAND_TOKEN.match(text)
    if not lead:
        return False
    lead_name = lead.group(0)
    if _brand_equivalence_keys(lead_name) & brand_keys:
        return False
    if not _looks_like_known_brand(lead_name):
        return False
    return _brand_key(_canonical_brand_label(lead_name)) != _brand_key(_canonical_brand_label(brand))


def _looks_like_known_brand(name: str) -> bool:
    raw = ' '.join(str(name or '').split())
    if not raw:
        return False
    key = _brand_key(raw)
    if key in BRAND_CANONICAL_BY_KEY or key in PROPOSED_DICTIONARY_BRANDS:
        return True
    if _canonical_brand_label(raw) != raw:
        return True
    return Brand.objects.filter(name__iexact=raw).exists()


def _model_in_compatibility(compat: str, model: str) -> bool:
    name = ' '.join(str(model or '').split())
    if not name:
        return False
    text = str(compat or '')
    pattern = rf'(?<!\w){re.escape(name)}(?!\w)'
    matches = list(re.finditer(pattern, text, re.I))
    if not matches:
        return False
    has_plus = bool(re.search(r'\bplus\b', name, re.I))
    for match in matches:
        rest = text[match.end():]
        if not has_plus and re.match(r'\s+plus\b', rest, re.I):
            continue
        return True
    return False


def _merge_models_into_compatibility(
    compat: str,
    models: list[str] | None,
    *,
    brand_name: str = '',
) -> str:
    text = ' '.join(str(compat or '').split())
    extras: list[str] = []
    for raw in models or []:
        name = ' '.join(str(raw or '').split())
        if not name or _model_in_compatibility(text, name):
            continue
        extras.append(name)
        text = f'{text}, {name}' if text else name
    brand = ' '.join(str(brand_name or '').split())
    if brand and text and not re.search(rf'(?<!\w){re.escape(brand)}(?!\w)', text, re.I):
        if not (_brand_equivalence_keys(brand) & _brand_equivalence_keys(text.split()[0] if text else '')):
            # Keep textual models; brand prefix is optional when aliases already present.
            pass
    return text


def _blob_contains(value: str, blob: str) -> bool:
    compact_value = _brand_key(value)
    if len(compact_value) < 2:
        return False
    return compact_value in _brand_key(blob)


def _engine_is_supported(engine_text: str, *, compatibility: str, sources) -> bool:
    items = parse_plain_list(engine_text) or [
        item.strip() for item in str(engine_text or '').split('\n') if item.strip()
    ]
    if not items:
        return False
    source_blob = ' '.join(
        f"{item.get('title') or ''} {item.get('url') or ''}"
        for item in sources or []
        if isinstance(item, dict)
    )
    blob = f'{compatibility or ''} {source_blob}'
    for item in items:
        if not _blob_contains(item, blob) and item.casefold() not in (compatibility or '').casefold():
            return False
    return True


def _oem_is_supported(oem_text: str, *, article: str, sources) -> bool:
    tokens = [item for item in str(oem_text or '').split('\n') if item.strip()]
    if not tokens:
        return False
    article_key = normalize_article(article)
    if article_key and all(normalize_article(token) == article_key for token in tokens):
        return False
    source_blob = ' '.join(
        f"{item.get('title') or ''} {item.get('url') or ''}"
        for item in sources or []
        if isinstance(item, dict)
    )
    if any(_blob_contains(token, source_blob) for token in tokens):
        return True
    return len([item for item in sources or [] if isinstance(item, dict) and item.get('url')]) >= 2


def _description_mentions_blocked(text: str, blocked_values: list[str]) -> bool:
    blob = str(text or '')
    if detect_internal_research_text(blob):
        return True
    for raw in blocked_values:
        value = ' '.join(str(raw or '').split())
        if len(value) < 3:
            continue
        if value.casefold() in blob.casefold():
            return True
    return False


def _structured_description_inputs(
    *,
    product: Product,
    fields: dict[str, Any],
    field_decisions: dict[str, str],
) -> dict[str, str]:
    """Public description may use only approved or clean current structured facts."""
    current_title = _preview_current_clean(product, 'title')
    current_brand = product.brand.name if product.brand_id and product.brand else ''
    current_category = (
        product.category.name if product.category_id and product.category else ''
    )
    current_compat = _preview_current_clean(product, 'compatibility')
    if field_decisions.get('title') == 'approved':
        title = fields.get('title') or ''
    else:
        title = current_title
    if field_decisions.get('brand') == 'approved':
        brand = fields.get('brand_name') or ''
    else:
        brand = current_brand
    if field_decisions.get('category') == 'approved':
        category = fields.get('category_name') or ''
    else:
        category = current_category
    if field_decisions.get('compatibility') == 'approved':
        compatibility = fields.get('compatibility') or ''
    else:
        compatibility = current_compat
    engine = ''
    if field_decisions.get('engine_compatibility') == 'approved':
        engine = fields.get('engine_compatibility') or ''
    return {
        'title': title,
        'brand': brand,
        'category': category,
        'compatibility': compatibility,
        'engine_compatibility': engine,
    }


def _description_from_approved(values: dict[str, str]) -> str:
    parts: list[str] = []
    title = ' '.join(str(values.get('title') or '').split())
    brand = ' '.join(str(values.get('brand') or '').split())
    category = ' '.join(str(values.get('category') or '').split())
    compatibility = ' '.join(str(values.get('compatibility') or '').split())
    engines = str(values.get('engine_compatibility') or '').strip()
    if title:
        parts.append(title.rstrip('.') + '.')
    elif category and brand:
        parts.append(f'{category} {brand}.')
    elif category:
        parts.append(f'{category}.')
    elif brand:
        parts.append(f'{brand}.')
    if compatibility:
        parts.append(f'Применимость: {compatibility}.')
    if engines:
        engine_line = ', '.join(
            item for item in engines.split('\n') if item.strip()
        )
        if engine_line:
            parts.append(f'Двигатели: {engine_line}.')
    if not parts:
        return ''
    parts.append('Перед установкой сверьте применимость по VIN.')
    return ' '.join(parts)


def _oem_preview_value(raw: str, *, queried_article: str) -> tuple[str, str]:
    """Return (public_oem, rejected_note). Blank public OEM if the list looks fragmented."""
    return sanitize_oem_research(raw, queried_article=queried_article)


def _new_fact_gate(
    *,
    web_search_used: bool,
    source_count: int,
    article: str,
    value: str,
    sources,
) -> str:
    """Return 'public', 'notes', or 'drop'."""
    text = str(value or '').strip()
    if not text:
        return 'drop'
    if not web_search_used or source_count <= 0:
        return 'notes'
    if source_count == 1:
        if _article_tied_to_value(article, value=text, sources=sources):
            return 'public'
        return 'notes'
    return 'public'


def _build_field_decisions(
    *,
    product: Product,
    fields: dict[str, Any],
    new_public: set[str],
    blocked_reasons: dict[str, str],
    field_gates: dict[str, str],
) -> dict[str, str]:
    current = {
        'title': _preview_current_clean(product, 'title'),
        'brand': product.brand.name if product.brand_id and product.brand else '',
        'category': product.category.name if product.category_id and product.category else '',
        'compatibility': _preview_current_clean(product, 'compatibility'),
        'engine_compatibility': _preview_current_clean(product, 'engine_compatibility'),
        'oem_cross_references': _preview_current_clean(product, 'oem_cross_references'),
        'description': _preview_current_clean(product, 'description'),
    }
    suggested = {
        'title': fields.get('title') or '',
        'brand': fields.get('brand_name') or '',
        'category': fields.get('category_name') or '',
        'compatibility': fields.get('compatibility') or '',
        'engine_compatibility': fields.get('engine_compatibility') or '',
        'oem_cross_references': fields.get('oem_cross_references') or '',
        'description': fields.get('description') or '',
    }
    decisions: dict[str, str] = {}
    for name in APPROVAL_FIELDS:
        if name == 'description':
            decisions[name] = 'unchanged'
            continue
        if name in blocked_reasons:
            decisions[name] = 'blocked'
            continue
        if name in new_public and str(suggested.get(name) or '').strip():
            decisions[name] = 'approved'
            continue
        current_value = str(current.get(name) or '').strip()
        suggested_value = str(suggested.get(name) or '').strip()
        if current_value and suggested_value == current_value:
            decisions[name] = 'unchanged'
            continue
        if not suggested_value:
            decisions[name] = 'unchanged'
            continue
        if suggested_value and not current_value and field_gates.get(name) == 'public':
            decisions[name] = 'approved'
            continue
        decisions[name] = 'unchanged'
    return decisions


def preview_enrichment_for_product(
    product: Product,
    *,
    openai_caller: Callable[..., OpenAIEnrichment | None] | None = None,
) -> dict[str, Any]:
    """Evidence-based AI enrichment preview. Never writes the row."""
    captured: dict[str, OpenAIEnrichment | None] = {'enrichment': None}
    inner = openai_caller or call_openai_product_lookup

    def wrapped_caller(article, local_fields, **kwargs):
        kwargs.setdefault('research_mode', True)
        result = _invoke_openai_caller(inner, article, local_fields, **kwargs)
        captured['enrichment'] = result
        return result

    suggestion = suggest_product_by_article(
        product.article or '',
        openai_caller=wrapped_caller,
        exclude_product_id=product.pk,
        research_mode=True,
    )
    enrichment = captured.get('enrichment')
    article = product.article or ''
    sources = _dedupe_sources(
        list(suggestion.get('sources') or [])
        + (list(enrichment.sources) if enrichment is not None else [])
    )
    web_search_used = bool(
        suggestion.get('web_search_used')
        or (enrichment.web_search_used if enrichment is not None else False)
    )
    source_count = len({item['url'] for item in sources if item.get('url')})
    evidence_notes: list[dict[str, str]] = [
        {
            'text': 'Целевая карточка исключена из локальных доказательств по артикулу.',
            'severity': 'info',
        }
    ]
    if not web_search_used:
        evidence_notes.append({
            'text': 'web_search не выполнен — новые факты не подтверждены.',
            'severity': 'warning',
        })
    if source_count == 0:
        evidence_notes.append({
            'text': 'Внешние URL не извлечены (source_count=0).',
            'severity': 'warning',
        })

    fields = dict(suggestion.get('fields') or {})
    notes = list(normalize_research_notes(suggestion.get('research_notes')))
    unmatched = list(suggestion.get('unmatched') or [])

    fields['title'] = prefer_public_field(
        product.title or '',
        (enrichment.title if enrichment is not None else fields.get('title') or ''),
        field='title',
    )
    fields['title'] = _preview_clean_text(fields.get('title') or '', field='title')
    fields['description'] = prefer_public_field(
        product.description or '',
        (enrichment.description if enrichment is not None else fields.get('description') or ''),
        field='description',
    )
    fields['description'] = _preview_clean_text(
        fields.get('description') or '',
        field='description',
    )

    current_brand = product.brand.name if product.brand_id and product.brand else ''
    ai_brand = (enrichment.brand if enrichment is not None else '') or ''
    current_category = product.category.name if product.category_id and product.category else ''
    ai_category = (enrichment.category if enrichment is not None else '') or ''
    ai_models = list(enrichment.models) if enrichment is not None else []
    dictionary_additions: dict[str, list[str]] = {'brands': [], 'categories': []}
    blocked_reasons: dict[str, str] = {}
    new_public: set[str] = set()
    field_gates: dict[str, str] = {}

    for name in _PREVIEW_FITMENT_FIELDS:
        current_clean = _preview_current_clean(product, name)
        ai_raw = getattr(enrichment, name, '') if enrichment is not None else ''
        if name == 'oem_cross_references':
            ai_clean, rejected_raw = _oem_preview_value(ai_raw or '', queried_article=article)
            if rejected_raw and not ai_clean:
                notes.append({
                    'text': f'oem_cross_references: фрагментированный/невалидный список отклонён: {rejected_raw}',
                    'severity': 'warning',
                })
                evidence_notes.append({
                    'text': 'OEM_FRAGMENTED: публичный OEM очищен.',
                    'severity': 'warning',
                })
                blocked_reasons['oem_cross_references'] = 'questionable OEM'
        else:
            ai_clean = _preview_clean_text(
                ai_raw or '',
                field=name,
                queried_article=article,
            )
        if name == 'compatibility':
            ai_clean = _merge_models_into_compatibility(
                ai_clean,
                ai_models,
                brand_name=_canonical_brand_label(ai_brand) or ai_brand,
            )
        decision = _new_fact_gate(
            web_search_used=web_search_used,
            source_count=source_count,
            article=article,
            value=ai_clean,
            sources=sources,
        )
        field_gates[name] = decision
        if current_clean:
            merged = current_clean
            if name == 'compatibility' and decision == 'public':
                merged = _merge_models_into_compatibility(
                    current_clean,
                    ai_models,
                    brand_name=current_brand or _canonical_brand_label(ai_brand),
                )
            fields[name] = merged
            if (
                name == 'compatibility'
                and decision == 'public'
                and ' '.join(str(merged or '').split())
                != ' '.join(str(current_clean or '').split())
            ):
                new_public.add('compatibility')
            if ai_clean and ai_clean.strip() != current_clean.strip():
                notes.append({
                    'text': (
                        f'Текущее поле «{name}» сохранено как CURRENT, '
                        f'не как доказательство. По артикулу также: {ai_clean}'
                    ),
                    'severity': 'info',
                })
            continue
        if decision == 'public':
            fields[name] = ai_clean
            new_public.add(name)
        else:
            if ai_clean:
                notes.append({
                    'text': (
                        f'{name}: недостаточно независимых источников '
                        f'(source_count={source_count}). Кандидат: {ai_clean}'
                    ),
                    'severity': 'warning',
                })
                blocked_reasons[name] = 'unresolved evidence'
            fields[name] = ''

    if current_brand:
        fields['brand_id'] = product.brand_id
        fields['brand_name'] = current_brand
        fields['country_id'] = product.brand.country_id if product.brand else fields.get('country_id')
        fields['country_name'] = (
            product.brand.country.name
            if product.brand_id and product.brand and product.brand.country_id
            else fields.get('country_name') or ''
        )
        field_gates['brand'] = 'current'
    else:
        brand_decision = _new_fact_gate(
            web_search_used=web_search_used,
            source_count=source_count,
            article=article,
            value=ai_brand,
            sources=sources,
        )
        field_gates['brand'] = brand_decision
        matched_brand = _match_brand(ai_brand) if ai_brand else None
        proposed_brand = _proposed_dictionary_brand(ai_brand) if ai_brand else ''
        canonical_brand = _canonical_brand_label(ai_brand) if ai_brand else ''
        if brand_decision == 'public' and matched_brand is not None:
            fields['brand_id'] = matched_brand.pk
            fields['brand_name'] = matched_brand.name
            fields['country_id'] = matched_brand.country_id
            fields['country_name'] = (
                matched_brand.country.name if matched_brand.country_id else ''
            )
            new_public.add('brand')
        elif brand_decision == 'public' and proposed_brand:
            fields['brand_id'] = None
            fields['brand_name'] = proposed_brand
            if proposed_brand not in dictionary_additions['brands']:
                dictionary_additions['brands'].append(proposed_brand)
            blocked_reasons['brand'] = 'missing required Brand'
            new_public.add('brand')
            notes.append({
                'text': (
                    f'brand: внешние источники подтверждают «{proposed_brand}», '
                    'но Brand в справочнике нет. Apply заблокирован до добавления марки.'
                ),
                'severity': 'warning',
            })
        else:
            if ai_brand:
                notes.append({
                    'text': (
                        f'brand: новый факт без достаточных источников. Кандидат: {ai_brand}'
                    ),
                    'severity': 'warning',
                })
                blocked_reasons['brand'] = 'unresolved evidence'
            if ai_brand and matched_brand is None and not proposed_brand:
                unmatched.append(
                    f'Марка «{canonical_brand or ai_brand}» не найдена в справочнике. '
                    'Выберите марку вручную.'
                )
            fields['brand_id'] = None
            fields['brand_name'] = ''

    if current_category:
        fields['category_id'] = product.category_id
        fields['category_name'] = current_category
        field_gates['category'] = 'current'
    else:
        matched_category = _match_category(
            ai_category,
            title=fields.get('title') or product.title or '',
        )
        if matched_category is None and (ai_category or product.title):
            matched_category = _match_category(
                FILTER_CATEGORY_NAME if _is_filter_category_alias(ai_category or product.title) else ai_category,
                title=product.title or '',
            )
        category_gate = _new_fact_gate(
            web_search_used=web_search_used,
            source_count=source_count,
            article=article,
            value=ai_category or (matched_category.name if matched_category else ''),
            sources=sources,
        )
        field_gates['category'] = category_gate
        if matched_category is not None:
            fields['category_id'] = matched_category.pk
            fields['category_name'] = matched_category.name
            if category_gate == 'public':
                new_public.add('category')
            elif not web_search_used or source_count == 0:
                blocked_reasons.setdefault('category', 'unresolved evidence')
        elif ai_category:
            unmatched.append(
                f'Категория «{ai_category}» не найдена в справочнике. '
                'Выберите ближайшую вручную.'
            )
            blocked_reasons['category'] = 'missing required Category'
            fields['category_id'] = None
            fields['category_name'] = ''
        else:
            fields['category_id'] = None
            fields['category_name'] = ''

    if product.car_model_id and product.car_model:
        fields['car_model_id'] = product.car_model_id
        fields['car_model_name'] = product.car_model.name

    suggested_brand = fields.get('brand_name') or ''
    suggested_compat = fields.get('compatibility') or ''
    ai_compat = _merge_models_into_compatibility(
        _preview_clean_text(
            (enrichment.compatibility if enrichment is not None else '') or '',
            field='compatibility',
        ),
        ai_models,
        brand_name=_canonical_brand_label(ai_brand) or suggested_brand,
    )
    conflict_brand = suggested_brand or _canonical_brand_label(ai_brand) or ai_brand
    conflict_compat = suggested_compat or ai_compat
    conflict = brand_compatibility_conflict(conflict_brand, conflict_compat)
    if not conflict and conflict_brand and ai_compat:
        conflict = brand_compatibility_conflict(conflict_brand, ai_compat)
    if conflict:
        evidence_notes.append({
            'text': (
                f'{ISSUE_BRAND_COMPATIBILITY_CONFLICT}: '
                f'марка «{conflict_brand}» не согласуется с применимостью «{conflict_compat}».'
            ),
            'severity': 'warning',
        })
        notes.append({
            'text': (
                f'{ISSUE_BRAND_COMPATIBILITY_CONFLICT}: не сопоставлять '
                f'«{conflict_brand}» с «{conflict_compat}».'
            ),
            'severity': 'warning',
        })
        blocked_reasons['brand'] = 'BRAND_COMPATIBILITY_CONFLICT'
        blocked_reasons['compatibility'] = 'BRAND_COMPATIBILITY_CONFLICT'
        if not current_brand:
            fields['brand_id'] = None
            fields['brand_name'] = ''
            suggested_brand = ''
            dictionary_additions['brands'] = []
            new_public.discard('brand')
        if not _preview_current_clean(product, 'compatibility'):
            if ai_compat:
                notes.append({
                    'text': f'compatibility: кандидат из‑за конфликта марки оставлен в notes: {ai_compat}',
                    'severity': 'warning',
                })
            fields['compatibility'] = ''
            new_public.discard('compatibility')
    elif fields.get('compatibility'):
        fields['compatibility'] = _merge_models_into_compatibility(
            fields.get('compatibility') or '',
            ai_models,
            brand_name=suggested_brand or _canonical_brand_label(ai_brand),
        )

    engine_text = str(fields.get('engine_compatibility') or '').strip()
    if engine_text:
        if not _engine_is_supported(
            engine_text,
            compatibility=str(fields.get('compatibility') or ''),
            sources=sources,
        ):
            blocked_reasons['engine_compatibility'] = 'unsupported engine'
            notes.append({
                'text': (
                    f'engine_compatibility: значение не подтверждено совместимостью/источниками: {engine_text}'
                ),
                'severity': 'warning',
            })
            new_public.discard('engine_compatibility')

    oem_text = str(fields.get('oem_cross_references') or '').strip()
    if oem_text:
        if not _oem_is_supported(oem_text, article=article, sources=sources) and source_count < 2:
            blocked_reasons['oem_cross_references'] = 'insufficient OEM evidence'
            new_public.discard('oem_cross_references')

    current_title = _preview_current_clean(product, 'title')
    ai_title = _preview_clean_text(
        (enrichment.title if enrichment is not None else '') or '',
        field='title',
    )
    if current_title:
        field_gates['title'] = 'current'
    elif ai_title:
        title_gate = _new_fact_gate(
            web_search_used=web_search_used,
            source_count=source_count,
            article=article,
            value=ai_title,
            sources=sources,
        )
        field_gates['title'] = title_gate
        if title_gate == 'public':
            new_public.add('title')
        else:
            blocked_reasons.setdefault('title', 'unresolved evidence')
    else:
        field_gates['title'] = 'drop'

    public_blob = ' '.join(str(fields.get(name) or '') for name in _PREVIEW_TEXT_FIELDS)
    if detect_internal_research_text(public_blob):
        for name in _PREVIEW_TEXT_FIELDS:
            fields[name] = _preview_clean_text(
                fields.get(name) or '',
                field=name,
                queried_article=article,
            )

    fragmented_oem = any('OEM_FRAGMENTED' in item['text'] for item in evidence_notes)
    has_public_new_facts = bool(new_public)
    if conflict or fragmented_oem or not web_search_used or source_count == 0:
        confidence = 'needs_verification'
    elif source_count >= 2 and web_search_used and has_public_new_facts and not conflict:
        confidence = 'confirmed'
    elif source_count >= 1 and web_search_used and not conflict:
        confidence = 'likely'
    else:
        confidence = 'needs_verification'
    if blocked_reasons.get('brand') == 'missing required Brand' and web_search_used and source_count >= 2 and not conflict:
        confidence = 'confirmed'
    if not has_public_new_facts and (not web_search_used or source_count == 0):
        confidence = 'needs_verification'

    field_decisions = _build_field_decisions(
        product=product,
        fields=fields,
        new_public=new_public,
        blocked_reasons=blocked_reasons,
        field_gates=field_gates,
    )

    approved_for_description = _structured_description_inputs(
        product=product,
        fields=fields,
        field_decisions=field_decisions,
    )
    current_raw_description = product.description or ''
    current_description_clean = bool(str(current_raw_description).strip()) and not detect_internal_research_text(
        current_raw_description
    )
    ai_description_raw = (
        (enrichment.description if enrichment is not None else '') or ''
    )
    if str(ai_description_raw).strip():
        notes.append({
            'text': (
                'description: AI-текст не публикуется напрямую; '
                f'кандидат оставлен в notes: {ai_description_raw}'
            ),
            'severity': 'info',
        })
    if current_description_clean:
        fields['description'] = current_raw_description
        field_decisions['description'] = 'unchanged'
        new_public.discard('description')
    else:
        safe_generated = _preview_clean_text(
            _description_from_approved(approved_for_description),
            field='description',
        )
        if safe_generated:
            fields['description'] = safe_generated
            field_decisions['description'] = 'approved'
            new_public.add('description')
        else:
            fields['description'] = ''
            field_decisions['description'] = 'unchanged'
            new_public.discard('description')

    approved_fields = [name for name in APPROVAL_FIELDS if field_decisions.get(name) == 'approved']
    blocked_fields = [name for name in APPROVAL_FIELDS if field_decisions.get(name) == 'blocked']

    unresolved = _preview_unresolved(
        fields,
        unmatched=unmatched,
        confidence=confidence,
    )
    for item in unresolved:
        reason = str(item.get('reason') or '').strip()
        field_name = str(item.get('field') or '').strip()
        if reason:
            notes.append({
                'text': f'{field_name}: {reason}' if field_name else reason,
                'severity': 'warning',
            })
    notes = normalize_research_notes(notes)
    evidence_notes = normalize_research_notes(evidence_notes)

    return {
        'ok': bool(suggestion.get('ok')),
        'error': suggestion.get('error') or '',
        'ai_used': bool(suggestion.get('ai_used') or enrichment is not None),
        'ai_error': suggestion.get('ai_error') or '',
        'web_search_used': web_search_used,
        'source_count': source_count,
        'product_id': product.pk,
        'current_article': product.article or '',
        'current_title': product.title or '',
        'current_brand': current_brand,
        'current_brand_id': product.brand_id,
        'current_category': current_category,
        'current_category_id': product.category_id,
        'current_compatibility': product.compatibility or '',
        'current_engine_compatibility': product.engine_compatibility or '',
        'current_oem_cross_references': product.oem_cross_references or '',
        'current_description': product.description or '',
        'suggested_title': fields.get('title') or '',
        'suggested_brand': fields.get('brand_name') or '',
        'suggested_brand_id': fields.get('brand_id'),
        'suggested_category': fields.get('category_name') or '',
        'suggested_category_id': fields.get('category_id'),
        'suggested_compatibility': fields.get('compatibility') or '',
        'suggested_engine_compatibility': fields.get('engine_compatibility') or '',
        'suggested_oem_cross_references': fields.get('oem_cross_references') or '',
        'suggested_description': fields.get('description') or '',
        'research_notes': notes,
        'evidence_notes': evidence_notes,
        'sources': sources,
        'confidence': confidence,
        'unresolved_fields': unresolved,
        'unmatched': unmatched,
        'approved_fields': approved_fields,
        'blocked_fields': blocked_fields,
        'field_decisions': field_decisions,
        'dictionary_additions': dictionary_additions,
        'fields': fields,
    }



