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
