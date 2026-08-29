from django import template
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
