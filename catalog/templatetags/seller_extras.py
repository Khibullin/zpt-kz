import re

from django import template
from django.utils.text import Truncator

register = template.Library()


@register.inclusion_tag('catalog/includes/seller_avatar.html')
def seller_avatar(seller, size='lg', link='', wrapper_class='', title=''):
    if not title and link and seller:
        title = f'Профиль продавца {seller.name}'
    return {
        'seller': seller,
        'size': size,
        'link': link,
        'wrapper_class': wrapper_class,
        'title': title,
    }


@register.filter
def is_public_seller_profile_request(request):
    resolver_match = getattr(request, 'resolver_match', None)
    return getattr(resolver_match, 'url_name', '') == 'public_seller_profile'


@register.filter
def public_seller_meta_description(seller):
    """Build a compact factual description for an indexable seller storefront."""
    name = re.sub(r'\s+', ' ', str(getattr(seller, 'name', '') or '')).strip()
    city = re.sub(r'\s+', ' ', str(getattr(seller, 'city', '') or '')).strip()
    name = name or 'Продавец автозапчастей'

    if city:
        location = f' в {city}'
    else:
        location = ' в Казахстане'

    text = (
        f'{name} — продавец автозапчастей{location} на ZPT.KZ. '
        'Каталог товаров, контакты, условия покупки и доставки.'
    )
    return Truncator(text).chars(160, truncate='…')
