from __future__ import annotations

from xml.sax.saxutils import escape

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_GET

from catalog.legacy_product_urls import LEGACY_PRODUCT_SLUG_REDIRECTS
from catalog.models import Product
from core.seo import canonical_url_for_path


# Keep the first public sitemap intentionally small. Other public areas are
# added only after their page-level SEO/content review is complete.
STATIC_SITEMAP_PATHS = (
    '/',
    '/request-parts/',
    '/request-parts/guide/',
    '/request-parts/faq/',
    '/prodavat/',
)

MIN_PRODUCT_DESCRIPTION_LENGTH = 40


def _urlset(items: str = '') -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f'{items}'
        '</urlset>'
    )


def _product_is_sitemap_ready(product: Product) -> bool:
    return all((
        bool((product.slug or '').strip()),
        bool((product.title or '').strip()),
        bool(product.brand_id),
        bool(product.category_id),
        bool(product.main_image),
        len((product.description or '').strip()) >= MIN_PRODUCT_DESCRIPTION_LENGTH,
    ))


def _public_product_slug(stored_slug: str) -> str:
    return LEGACY_PRODUCT_SLUG_REDIRECTS.get(stored_slug, stored_slug)


@require_GET
def robots_txt(request):
    lines = [
        'User-agent: *',
        'Disallow: /admin/',
        'Disallow: /api/',
        'Disallow: /marketing/',
        'Disallow: /ajax/',
        'Disallow: /catalog/ajax/',
        'Disallow: /market/ajax/',
        'Disallow: /go/',
        'Disallow: /r/',
        'Allow: /static/',
        'Allow: /products/',
        '',
        f'Sitemap: {canonical_url_for_path("/sitemap.xml")}',
        '',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')


@require_GET
def sitemap_index(request):
    urls = [canonical_url_for_path('/sitemap-static.xml')]
    if getattr(settings, 'SEO_PRODUCT_SITEMAP_ENABLED', False):
        urls.append(canonical_url_for_path('/sitemap-products.xml'))

    items = ''.join(f'<sitemap><loc>{escape(url)}</loc></sitemap>' for url in urls)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f'{items}'
        '</sitemapindex>'
    )
    return HttpResponse(xml, content_type='application/xml; charset=utf-8')


@require_GET
def sitemap_static(request):
    items = ''.join(
        f'<url><loc>{escape(canonical_url_for_path(path))}</loc></url>'
        for path in STATIC_SITEMAP_PATHS
    )
    return HttpResponse(_urlset(items), content_type='application/xml; charset=utf-8')


@require_GET
def sitemap_products(request):
    if not getattr(settings, 'SEO_PRODUCT_SITEMAP_ENABLED', False):
        return HttpResponse(_urlset(), content_type='application/xml; charset=utf-8')

    seen_slugs: set[str] = set()
    items: list[str] = []

    products = Product.objects.filter(status='active').exclude(slug='').order_by('pk').only(
        'slug',
        'title',
        'brand_id',
        'category_id',
        'main_image',
        'description',
        'updated_at',
    )
    for product in products.iterator():
        if not _product_is_sitemap_ready(product):
            continue

        public_slug = _public_product_slug(product.slug)
        if public_slug in seen_slugs:
            continue
        seen_slugs.add(public_slug)

        loc = escape(canonical_url_for_path(f'/{public_slug}/'))
        lastmod = product.updated_at.date().isoformat()
        items.append(f'<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>')

    return HttpResponse(
        _urlset(''.join(items)),
        content_type='application/xml; charset=utf-8',
    )
