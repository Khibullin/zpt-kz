import json
import re

from django import template
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.text import Truncator

register = template.Library()


@register.filter
def whatsapp_phone(value):
    from core.phone_utils import normalize_phone_for_whatsapp

    return normalize_phone_for_whatsapp(value) or ''


@register.filter
def whatsapp_url(value, text=None):
    from core.phone_utils import build_whatsapp_url

    return build_whatsapp_url(value, text)


@register.filter
def contains_icase(haystack, needle):
    if not haystack or not needle:
        return False
    return str(needle).lower() in str(haystack).lower()


@register.filter
def vehicle_line(product):
    from catalog.applicability import vehicle_line_if_not_in_title

    return vehicle_line_if_not_in_title(product)


@register.filter
def public_card_title(product):
    """Use exact reviewed translations and avoid repeating a trailing article."""
    from catalog.product_display import public_product_title
    title = public_product_title(product)
    article = str(getattr(product, 'article', '') or '').strip()
    if article:
        title = re.sub(r'\s+[—–-]\s*' + re.escape(article) + r'\s*$', '', title, flags=re.I)
    return title.rstrip(' —–') or str(getattr(product, 'title', '') or '')


@register.filter
def public_product_seo_name(product):
    """Public product title with an article added once, when available."""
    from catalog.product_display import public_product_title

    title = re.sub(r'\s+', ' ', public_product_title(product)).strip()
    article = re.sub(r'\s+', ' ', str(getattr(product, 'article', '') or '')).strip()
    if not article:
        return title
    if re.search(r'(?<![\w])' + re.escape(article) + r'(?![\w])', title, flags=re.I):
        return title
    return f'{title} {article}'.strip()


@register.filter
def public_card_fitment(product):
    from catalog.applicability import public_card_fitment as build_public_card_fitment

    return build_public_card_fitment(product)


@register.filter
def public_product_url(product):
    """Return the public canonical path for a product without a legacy redirect hop."""
    from catalog.fitment_slug_redirects import public_product_slug

    slug = (getattr(product, 'slug', '') or '').strip()
    if slug:
        public_slug = public_product_slug(slug)
        return reverse('product_detail', kwargs={'slug': public_slug})
    return reverse('product_detail_old', kwargs={'pk': product.pk})


@register.filter
def public_product_whatsapp_message(product):
    """Keep the existing inquiry wording but replace a legacy product URL with canonical."""
    message = product.get_whatsapp_inquiry_message()
    slug = (getattr(product, 'slug', '') or '').strip()
    if not slug:
        return message

    legacy_url = f'https://zpt.kz/{slug}/'
    canonical_url = f'https://zpt.kz{public_product_url(product)}'
    return message.replace(legacy_url, canonical_url)


@register.filter
def is_public_product_detail_request(request):
    """True only for public product detail views, including canonical/numeric routes."""
    resolver_match = getattr(request, 'resolver_match', None)
    view_func = getattr(resolver_match, 'func', None)
    return getattr(view_func, '__name__', '') in {
        'product_detail',
        'canonical_product_alias',
        'numeric_product_entry',
        'fitment_canonical_alias',
    }


@register.filter
def public_product_meta_description(product):
    """Build a stable factual meta description from public product fields."""
    title = re.sub(r'\s+', ' ', str(getattr(product, 'title', '') or '')).strip()
    article = re.sub(r'\s+', ' ', str(getattr(product, 'article', '') or '')).strip()
    brand = getattr(product, 'brand', None)
    brand_name = re.sub(
        r'\s+',
        ' ',
        str(getattr(brand, 'name', '') or ''),
    ).strip()

    title_for_meta = Truncator(title or 'Автозапчасть').chars(82, truncate='…')
    parts = [f'{title_for_meta}.']
    if article:
        parts.append(f'Арт. {article}.')
    if brand_name and brand_name.lower() not in title.lower():
        parts.append(f'Марка {brand_name}.')
    parts.append(
        'Купить в Казахстане на ZPT.KZ: цена, наличие, применяемость и контакты продавца.'
    )
    return Truncator(' '.join(parts)).chars(160, truncate='…')


def _absolute_public_url(path_or_url):
    value = str(path_or_url or '').strip()
    if not value:
        return ''
    if value.startswith(('http://', 'https://')):
        return value
    origin = (getattr(settings, 'PUBLIC_BASE_URL', '') or 'https://zpt.kz').rstrip('/')
    return f'{origin}/{value.lstrip("/")}'


def _safe_json_ld(data):
    payload = json.dumps(
        data,
        ensure_ascii=False,
        cls=DjangoJSONEncoder,
        separators=(',', ':'),
    )
    payload = (
        payload
        .replace('&', '\\u0026')
        .replace('<', '\\u003C')
        .replace('>', '\\u003E')
        .replace('\u2028', '\\u2028')
        .replace('\u2029', '\\u2029')
    )
    return mark_safe(payload)


@register.filter
def product_json_ld(product):
    """Serialize truthful schema.org Product data for a public product detail page."""
    canonical_url = _absolute_public_url(public_product_url(product))
    data = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        '@id': f'{canonical_url}#product',
        'url': canonical_url,
        'name': str(getattr(product, 'title', '') or '').strip(),
    }

    article = str(getattr(product, 'article', '') or '').strip()
    if article:
        data['sku'] = article

    description = str(getattr(product, 'description', '') or '').strip()
    if description:
        data['description'] = description

    # Product.brand identifies a *vehicle make*, not the product manufacturer.
    # Never publish it as the schema.org product brand. Only explicitly
    # identified house-branded items have reliable manufacturer evidence here.
    if re.search(r'(?<![\w])AG[\s-]+Parts(?![\w])', data['name'], re.I):
        data['brand'] = {'@type': 'Brand', 'name': 'AG Parts'}

    category = getattr(product, 'category', None)
    category_name = str(getattr(category, 'name', '') or '').strip()
    if category_name:
        data['category'] = category_name

    main_image = getattr(product, 'main_image', None)
    try:
        image_url = _absolute_public_url(main_image.url) if main_image else ''
    except (AttributeError, ValueError):
        image_url = ''
    if image_url:
        data['image'] = [image_url]

    condition = str(getattr(product, 'condition', '') or '').strip().lower()
    condition_map = {
        'new': 'https://schema.org/NewCondition',
        'used': 'https://schema.org/UsedCondition',
    }

    price = getattr(product, 'price', None)
    price_on_request = bool(getattr(product, 'price_on_request', False))
    if price is not None and not price_on_request:
        offer = {
            '@type': 'Offer',
            'url': canonical_url,
            'priceCurrency': 'KZT',
            'price': price,
        }
        if condition in condition_map:
            offer['itemCondition'] = condition_map[condition]

        # Keep Schema.org aligned with the same stock policy used on the
        # public product page; an unknown balance must not claim InStock.
        from catalog.wholesale import public_stock_status

        stock_code = public_stock_status(product)['code']
        if stock_code in ('in', 'out'):
            offer['availability'] = (
                'https://schema.org/InStock'
                if stock_code == 'in'
                else 'https://schema.org/OutOfStock'
            )
        data['offers'] = offer

    return _safe_json_ld(data)



@register.filter
def kit_json_ld(kit, view=None):
    """Maintenance kit metadata without a potentially misleading fixed Offer.

    Buyers may remove items, so the total changes with their selection.
    Vehicle brand is not the brand/manufacturer of this bundle.
    """
    url = _absolute_public_url(reverse('maintenance_kit_detail', kwargs={'slug': kit.slug}))
    data = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        '@id': f'{url}#kit',
        'name': str(kit.name or '').strip(),
        'url': url,
        'category': 'Комплект ТО',
    }
    if getattr(kit, 'description', ''):
        data['description'] = str(kit.description).strip()
    # The approved kit UI may render a verified collage rather than the raw
    # uploaded cover. Only describe the exact image actually shown to buyers.
    visible_cover_url = str(getattr(view, 'cover_image_url', '') or '').strip()
    if visible_cover_url:
        data['image'] = [_absolute_public_url(visible_cover_url)]
    marking = getattr(kit, 'marking', '')
    if marking:
        data['sku'] = marking
    return _safe_json_ld(data)
