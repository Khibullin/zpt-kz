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
    sanitize_oem_text,
    sanitize_public_product_text,
    split_public_sentences,
)

logger = logging.getLogger(__name__)

OPENAI_RESPONSES_URL = 'https://api.openai.com/v1/responses'
DEFAULT_PRODUCT_AI_MODEL = 'gpt-5.6-luna'
OPENAI_TIMEOUT = 25
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


def find_products_by_article(article: str) -> list[Product]:
    raw = display_article(article)
    key = normalize_article(raw)
    if not key:
        return []

    qs = Product.objects.select_related(
        'brand',
        'brand__country',
        'car_model',
        'category',
    ).prefetch_related('selected_models', 'selected_brands')

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
        if product.pk in matched_ids:
            continue
        if normalize_article(product.article) == key:
            matches.append(product)
            matched_ids.add(product.pk)
    return matches


def _match_category(name: str, *, title: str = '') -> Category | None:
    text = (name or '').strip()
    blob = f'{text} {title or ""}'
    if re.search(r'свеч|spark\s*plug|зажиган', blob, re.I):
        for preferred in SPARK_CATEGORY_NAMES:
            found = Category.objects.filter(name__iexact=preferred).first()
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


def _match_brand(name: str) -> Brand | None:
    text = (name or '').strip()
    if not text:
        return None
    qs = Brand.objects.select_related('country').filter(name__iexact=text).order_by(
        'country__name',
        'name',
        'id',
    )
    found = list(qs[:5])
    if found:
        return found[0]
    contains = list(
        Brand.objects.select_related('country').filter(name__icontains=text).order_by(
            'name',
            'id',
        )[:3]
    )
    if len(contains) == 1:
        return contains[0]
    return None


def _match_country(name: str) -> Country | None:
    text = (name or '').strip()
    if not text:
        return None
    return Country.objects.filter(name__iexact=text).order_by('id').first()


def _match_car_model(name: str, brand: Brand | None) -> CarModel | None:
    text = (name or '').strip()
    if not text:
        return None
    qs = CarModel.objects.select_related('brand', 'brand__country').filter(name__iexact=text)
    if brand is not None:
        branded = qs.filter(brand=brand).order_by('id')
        found = branded.first()
        if found:
            return found
    found = qs.order_by('id').first()
    if found:
        return found
    contains = CarModel.objects.select_related('brand', 'brand__country').filter(
        name__icontains=text,
    )
    if brand is not None:
        contains = contains.filter(brand=brand)
    items = list(contains.order_by('id')[:2])
    if len(items) == 1:
        return items[0]
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


def _walk_openai_text(payload: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
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
    for item in payload.get('output') or []:
        if not isinstance(item, dict):
            continue
        for content in item.get('content') or []:
            if not isinstance(content, dict):
                continue
            for annotation in content.get('annotations') or []:
                if not isinstance(annotation, dict):
                    continue
                url = str(annotation.get('url') or annotation.get('href') or '').strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                sources.append({
                    'title': str(annotation.get('title') or annotation.get('text') or url),
                    'url': url,
                })
    return text, sources


def call_openai_product_lookup(
    article: str,
    local_fields: dict[str, Any],
    *,
    urlopen: Callable[..., Any] | None = None,
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
    body = json.dumps({
        'model': model,
        'tools': [{'type': 'web_search'}],
        'input': prompt,
    }).encode('utf-8')
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
    try:
        with open_url(http_request, timeout=OPENAI_TIMEOUT) as response:
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
    text, sources = _walk_openai_text(payload)
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
    )
    return _sanitize_enrichment(enrichment)


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


def _sanitize_enrichment(enrichment: OpenAIEnrichment) -> OpenAIEnrichment:
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
            cleaned = sanitize_oem_text(raw)
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


def suggest_product_by_article(
    article: str,
    *,
    openai_caller: Callable[..., OpenAIEnrichment | None] | None = None,
) -> dict[str, Any]:
    raw = display_article(article)
    key = normalize_article(raw)
    if not key:
        return {
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
        }

    products = find_products_by_article(raw)
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
        enrichment = caller(raw, ai_context)
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
        enrichment = _sanitize_enrichment(enrichment)
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
            sanitize_oem_text(enrichment.oem_cross_references),
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
        if brand is None and enrichment.brand:
            brand = _match_brand(enrichment.brand)
            if brand is None:
                unmatched.append(
                    f'Марка «{enrichment.brand}» не найдена в справочнике. '
                    'Выберите марку вручную.'
                )
            else:
                country = brand.country
        for model_name in enrichment.models:
            matched_model = _match_car_model(model_name, brand)
            if matched_model is None:
                unmatched.append(
                    f'Модель «{model_name}» не найдена в справочнике. '
                    'Выберите ближайшую вручную.'
                )
                continue
            if brand is None:
                brand = matched_model.brand
                country = brand.country if brand is not None else country
            if car_model is None:
                car_model = matched_model
            elif matched_model.pk != car_model.pk:
                if all(item.pk != matched_model.pk for item in selected_models):
                    selected_models.append(matched_model)

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
    }
