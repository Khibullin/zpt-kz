import re

from django import template

register = template.Library()


def _digits(phone):
    return re.sub(r'\D', '', str(phone or ''))


@register.filter
def format_phone(value):
    digits = _digits(value)

    if digits.startswith('8') and len(digits) in (10, 11):
        digits = '7' + digits[1:]

    while len(digits) > 11 and digits.startswith('7'):
        digits = digits[1:]

    if len(digits) == 10:
        digits = '7' + digits

    if len(digits) == 11 and digits.startswith('7'):
        return (
            f'+{digits[0]} ({digits[1:4]}) '
            f'{digits[4:7]}-{digits[7:9]}-{digits[9:11]}'
        )

    return value or ''


@register.filter
def comma_to_space(value):
    return str(value).replace(',', ' ')


@register.filter
def whatsapp_phone(value):
    from core.phone_utils import normalize_phone_for_whatsapp

    return normalize_phone_for_whatsapp(value) or ''


@register.filter
def whatsapp_url(value, text=None):
    from core.phone_utils import build_whatsapp_url

    return build_whatsapp_url(value, text)
