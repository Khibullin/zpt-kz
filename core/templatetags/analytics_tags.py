import os
import re

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

_GTM_RE = re.compile(r'^GTM-[A-Z0-9]+$')


def _gtm_id():
    value = (os.getenv('GOOGLE_TAG_MANAGER_ID') or '').strip().upper()
    return value if _GTM_RE.fullmatch(value) else ''


@register.simple_tag
def google_tag_manager_head():
    container_id = _gtm_id()
    if not container_id:
        return ''
    return mark_safe(
        "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':"
        "+new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],"
        "j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src="
        "'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);"
        f"}})(window,document,'script','dataLayer','{container_id}');</script>"
    )


@register.simple_tag
def google_tag_manager_body():
    container_id = _gtm_id()
    if not container_id:
        return ''
    return mark_safe(
        '<noscript><iframe src="https://www.googletagmanager.com/ns.html?id='
        f'{container_id}" height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>'
    )
