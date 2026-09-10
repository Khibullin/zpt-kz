from django import template
from django.urls import reverse
import re

register = template.Library()


@register.filter
def whatsapp_phone(value):
    return re.sub(r'\D', '', str(value or ''))


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
def public_card_fitment(product):
    from catalog.applicability import public_card_fitment as build_public_card_fitment

    return build_public_card_fitment(product)


@register.filter
def public_product_url(product):
    """Return the public canonical path for a product without a legacy redirect hop."""
    from catalog.legacy_product_urls import LEGACY_PRODUCT_SLUG_REDIRECTS

    slug = (getattr(product, 'slug', '') or '').strip()
    if slug:
        public_slug = LEGACY_PRODUCT_SLUG_REDIRECTS.get(slug, slug)
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
