"""Read-only formatting for the dismantler directory list."""
import re
from django import template
from core.templatetags.dismantler_detail import clean_value, address_key, seller_specializations

register = template.Library()


@register.simple_tag
def directory_card(seller):
    name = re.sub(r'\s*[•|]\s*Instagram photos and videos\s*$', '', seller.name, flags=re.I).strip()
    location = clean_value(seller.market_location)
    if address_key(location) == address_key(seller.address):
        location = ''
    return {'name': name or seller.name, 'location': location,
            'specializations': seller_specializations(seller),
            'description': clean_value(seller.directory_description),
            'description_kk': clean_value(seller.directory_description_kk)}


@register.simple_tag
def directory_pages(page):
    return list(page.paginator.get_elided_page_range(page.number, on_each_side=1, on_ends=1))
