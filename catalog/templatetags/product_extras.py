from django import template

register = template.Library()


@register.filter
def contains_icase(haystack, needle):
    if not haystack or not needle:
        return False
    return str(needle).lower() in str(haystack).lower()
