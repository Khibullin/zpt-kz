from __future__ import annotations

import re

from django.http import HttpResponsePermanentRedirect

from core.seo import canonical_path, robots_directive
from core.templatetags.analytics_tags import (
    google_tag_manager_body,
    google_tag_manager_head,
)


REVIEWED_BRAND_LANDING_PATHS = {
    'changan': '/avtozapchasti/changan/',
    'chery': '/avtozapchasti/chery/',
    'geely': '/avtozapchasti/geely/',
    'haval': '/avtozapchasti/haval/',
    'jac': '/avtozapchasti/jac/',
    'zeekr': '/avtozapchasti/zeekr/',
}


def _reviewed_brand_filter_redirect(request):
    """Move a clean brand-only catalog filter to its reviewed SEO landing.

    Empty select values do not count as meaningful filters. A country, model,
    category, search query, city, sort, offer, or other content-changing value
    keeps the request on the normal catalog route.
    """
    if request.method not in {'GET', 'HEAD'}:
        return None
    if canonical_path(request.path) != '/':
        return None

    meaningful = {
        key: (value or '').strip()
        for key, value in request.GET.items()
        if (value or '').strip()
    }
    if not meaningful or 'brand' not in meaningful:
        return None
    if set(meaningful) - {'brand', 'all'}:
        return None

    brand_id = meaningful['brand']
    if not brand_id.isdigit():
        return None

    from catalog.models import Brand

    brand_name = (
        Brand.objects.filter(pk=int(brand_id))
        .values_list('name', flat=True)
        .first()
    )
    target = REVIEWED_BRAND_LANDING_PATHS.get(
        str(brand_name or '').strip().lower()
    )
    if not target:
        return None

    return HttpResponsePermanentRedirect(target)


def _inject_missing_gtm(response):
    """Ensure optional GTM coverage for standalone HTML templates.

    Normal pages already receive GTM through base.html/base_portal.html. A few
    legacy public templates are standalone; when a valid GTM container is
    configured, inject the same snippets into those responses without touching
    JSON/files/streaming responses or duplicating an existing snippet.
    """
    if getattr(response, 'streaming', False):
        return response
    if response.status_code >= 400:
        return response
    if response.get('Content-Encoding'):
        return response
    if not response.get('Content-Type', '').lower().startswith('text/html'):
        return response

    head = str(google_tag_manager_head())
    body = str(google_tag_manager_body())
    if not head:
        return response

    charset = response.charset or 'utf-8'
    html = response.content.decode(charset)
    if 'googletagmanager.com' in html:
        return response

    if '</head>' not in html.lower():
        return response

    html, head_count = re.subn(
        r'</head>',
        f'{head}</head>',
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if head_count and body:
        html = re.sub(
            r'(<body(?:\s[^>]*)?>)',
            rf'\1{body}',
            html,
            count=1,
            flags=re.IGNORECASE,
        )

    response.content = html.encode(charset)
    if response.has_header('Content-Length'):
        response['Content-Length'] = str(len(response.content))
    return response


class SeoRobotsHeaderMiddleware:
    """Canonicalize reviewed filters, SEO headers, and GTM HTML coverage."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        brand_redirect = _reviewed_brand_filter_redirect(request)
        if brand_redirect is not None:
            return brand_redirect

        response = self.get_response(request)
        directive = robots_directive(request)
        if directive.startswith('noindex'):
            response['X-Robots-Tag'] = directive
        return _inject_missing_gtm(response)
