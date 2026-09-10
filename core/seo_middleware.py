from __future__ import annotations

from django.http import HttpResponsePermanentRedirect

from core.seo import canonical_path, robots_directive


REVIEWED_BRAND_LANDING_PATHS = {
    'changan': '/avtozapchasti/changan/',
    'chery': '/avtozapchasti/chery/',
    'haval': '/avtozapchasti/haval/',
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


class SeoRobotsHeaderMiddleware:
    """Canonicalize reviewed brand filters and mirror noindex in X-Robots-Tag."""

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
        return response
