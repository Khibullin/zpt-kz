"""Product card presentation: vehicle subtitle and applicability block."""

from __future__ import annotations

import re

VIN_WARNING = (
    'Перед заказом рекомендуется проверить применяемость детали '
    'по VIN автомобиля.'
)

_LIST_SPLIT = re.compile(r'[\n;•|]+')
_HTML_TAG = re.compile(r'<[^>]+>')
_URL = re.compile(r'https?://', re.IGNORECASE)
_SEPARATORS = re.compile(r'[,;:/()•·|+\-–—]+')
_FILLER_WORDS = {
    'для', 'и', 'на', 'или', 'а', 'с', 'по', 'the', 'for', 'and', 'or', 'of',
    'подходит', 'применимость', 'совместимость', 'модель', 'модели',
    'марка', 'авто', 'автомобиля', 'автомобилей', 'также', 'compatible',
    'fitment', 'application', 'каталог', 'каталогам',
}


def parse_plain_list(raw):
    """Split engines/OEM text into unique values. No HTML, no URLs."""
    items = []
    seen = set()
    for part in _LIST_SPLIT.split(str(raw or '')):
        value = _HTML_TAG.sub('', part)
        value = ' '.join(value.split())
        if not value or _URL.search(value):
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(value)
    return items


def serialize_plain_list(raw):
    return '\n'.join(parse_plain_list(raw))


def vehicle_display_name(product):
    brand = ''
    model = ''
    if getattr(product, 'brand', None) and product.brand.name:
        brand = product.brand.name.strip()
    if getattr(product, 'car_model', None) and product.car_model.name:
        model = product.car_model.name.strip()
    return ' '.join(part for part in (brand, model) if part)


def title_contains_vehicle(product):
    line = vehicle_display_name(product)
    if not line:
        return True
    title = (product.title or '')
    return line.casefold() in title.casefold()


def vehicle_line_if_not_in_title(product):
    if title_contains_vehicle(product):
        return ''
    return vehicle_display_name(product)


def public_card_fitment(product):
    """Structured fitment for public product cards.

    Built only from brand, car_model, selected_brands, and selected_models.
    Never includes Product.compatibility, research notes, or supplier comments.
    """
    groups = grouped_applicability(product)
    bits = []
    for group in groups:
        brand_name = (group['brand'].name or '').strip()
        models = [
            (model.name or '').strip()
            for model in group['models']
            if (model.name or '').strip()
        ]
        if models:
            bits.append(f'{brand_name} {", ".join(models)}'.strip())
        elif brand_name:
            bits.append(brand_name)
    line = '; '.join(bit for bit in bits if bit)
    if not line:
        return ''
    title = (getattr(product, 'title', '') or '').casefold()
    if line.casefold() in title:
        return ''
    return line


def grouped_applicability(product):
    """Group primary + selected models by CarModel.brand. Deduplicate by pk."""
    models = []
    seen_ids = set()

    def add_model(model):
        if model is None or model.pk in seen_ids:
            return
        seen_ids.add(model.pk)
        models.append(model)

    add_model(getattr(product, 'car_model', None))
    selected_models = getattr(product, 'selected_models', None)
    if selected_models is not None:
        for model in selected_models.all():
            add_model(model)

    groups = {}
    for model in models:
        brand = model.brand
        if brand.pk not in groups:
            groups[brand.pk] = {'brand': brand, 'models': []}
        groups[brand.pk]['models'].append(model)

    selected_brands = getattr(product, 'selected_brands', None)
    if selected_brands is not None:
        for brand in selected_brands.all():
            if brand.pk not in groups:
                groups[brand.pk] = {'brand': brand, 'models': []}

    primary_brand = getattr(product, 'brand', None)
    if primary_brand is not None and primary_brand.pk not in groups:
        groups[primary_brand.pk] = {'brand': primary_brand, 'models': []}

    result = list(groups.values())
    result.sort(key=lambda item: item['brand'].name.casefold())
    for item in result:
        item['models'].sort(key=lambda model: model.name.casefold())
    return result


def structured_fitment_labels(groups):
    labels = []
    for group in groups:
        brand_name = (group['brand'].name or '').strip()
        if brand_name:
            labels.append(brand_name)
        for model in group['models']:
            model_name = (model.name or '').strip()
            if model_name:
                labels.append(model_name)
            if brand_name and model_name:
                labels.append(f'{brand_name} {model_name}')
    return sorted(set(labels), key=lambda item: (-len(item), item.casefold()))


def extra_compatibility_text(product, groups=None):
    """Original compatibility text if it adds facts beyond structured models.

    Does not rewrite Product.compatibility and does not create CarModel rows.
    """
    raw = (getattr(product, 'compatibility', '') or '').strip()
    if not raw:
        return ''
    if groups is None:
        groups = grouped_applicability(product)
    remainder = raw
    for label in structured_fitment_labels(groups):
        remainder = re.sub(re.escape(label), ' ', remainder, flags=re.IGNORECASE)
    remainder = _SEPARATORS.sub(' ', remainder)
    leftover = []
    for token in remainder.split():
        cleaned = token.strip('.').casefold()
        if not cleaned or cleaned in _FILLER_WORDS:
            continue
        leftover.append(cleaned)
    if not leftover:
        return ''
    return raw


def build_product_applicability(product):
    groups = grouped_applicability(product)
    engines = parse_plain_list(getattr(product, 'engine_compatibility', ''))
    oem_refs = parse_plain_list(getattr(product, 'oem_cross_references', ''))
    extra_compatibility = extra_compatibility_text(product, groups)
    has_structured = bool(groups or engines or oem_refs)
    has_block = has_structured or bool(extra_compatibility)
    show_vin = has_block
    return {
        'groups': groups,
        'engines': engines,
        'oem_refs': oem_refs,
        'extra_compatibility': extra_compatibility,
        'has_structured': has_structured,
        'has_block': has_block,
        'show_vin_warning': show_vin,
        'vin_warning': VIN_WARNING if show_vin else '',
    }
