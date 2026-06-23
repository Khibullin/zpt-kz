import re

from django import template

register = template.Library()


def _digits(phone):
    return re.sub(r'\D', '', str(phone or ''))


@register.filter
def format_phone(value):
    digits = _digits(value)

    if digits.startswith('8'):
        digits = '7' + digits[1:]

    while len(digits) > 11 and digits.startswith('7'):
        digits = digits[1:]

    if len(digits) == 11 and digits.startswith('7777'):
        digits = digits[1:]

    if len(digits) == 11 and digits.startswith('777'):
        return (
            f'+7 ({digits[0:3]}) '
            f'{digits[3:6]}-{digits[6:8]}-{digits[9:11]}'
        )

    if len(digits) == 11 and digits.startswith('7'):
        digits = digits[1:]

    if len(digits) == 10:
        return (
            f'+7 ({digits[0:3]}) '
            f'{digits[3:6]}-{digits[6:8]}-{digits[8:10]}'
        )

    return value or ''
