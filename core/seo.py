from __future__ import annotations

from django.conf import settings


CATALOG_FILTER_QUERY_KEYS = frozenset({
    'q',
    'country',
    'brand',
    'model',
    'category',
    'city',
    'offer',
    'all',
    'page',
    'sort',
})

REQUEST_PARTS_VARIANT_QUERY_KEYS = frozenset({
    'transport',
    'country',
    'brand',
    'model',
    'category',
    'city',
})

NOINDEX_FOLLOW_PREFIXES = (
    '/cart/',
    '/feedback/',
    '/register/',
    '/seller/register/',
    '/request-parts/register/',
    '/service-request/register/',
)

NOINDEX_NOFOLLOW_PREFIXES = (
    '/admin/',
    '/api/',
    '/marketing/',
    '/ajax/',
    '/catalog/ajax/',
    '/go/',
    '/r/',
    '/seller/whatsapp-consent/',
    '/seller/login/',
    '/seller/logout/',
    '/seller/dashboard/',
    '/seller/profile/',
    '/seller/add/',
    '/seller/edit/',
    '/seller/delete/',
    '/request-parts/cabinet/',
    '/my-request/',
    '/my-requests/',
    '/service-request/cabinet/',
    '/service-request/result/',
    '/orders/',
)


def canonical_path(path: str) -> str:
    """Return the single public path used as the canonical ZPT URL."""
    path = path or '/'
    if path in {'/market', '/market/'}:
        return '/'
    if path.startswith('/market/'):
        return path[len('/market'):]
    return path if path.startswith('/') else f'/{path}'


def canonical_url_for_path(path: str) -> str:
    origin = (getattr(settings, 'PUBLIC_BASE_URL', '') or 'https://zpt.kz').rstrip('/')
    return f'{origin}{canonical_path(path)}'


def robots_directive(request) -> str:
    raw_path = request.path or '/'
    path = canonical_path(raw_path)

    if any(path.startswith(prefix) for prefix in NOINDEX_NOFOLLOW_PREFIXES):
        return 'noindex, nofollow'

    if any(path.startswith(prefix) for prefix in NOINDEX_FOLLOW_PREFIXES):
        return 'noindex, follow'

    # /market/ is a legacy duplicate mount of the public catalog. Keep it
    # crawlable for canonical discovery, but do not let it become a second index.
    if raw_path in {'/market', '/market/'} or raw_path.startswith('/market/'):
        return 'noindex, follow'

    if path == '/' and CATALOG_FILTER_QUERY_KEYS.intersection(request.GET.keys()):
        return 'noindex, follow'

    # Ad click identifiers (gclid/wbraid/utm_*) stay indexable with a clean
    # canonical. Content-changing prefill parameters must not create SEO pages.
    if path == '/request-parts/' and REQUEST_PARTS_VARIANT_QUERY_KEYS.intersection(
        request.GET.keys()
    ):
        return 'noindex, follow'

    return 'index, follow'


def seo_context(request) -> dict[str, str]:
    return {
        'seo_canonical_url': canonical_url_for_path(request.path),
        'seo_robots': robots_directive(request),
    }
