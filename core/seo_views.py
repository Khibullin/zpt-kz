from __future__ import annotations

from xml.sax.saxutils import escape

from django.http import HttpResponse
from django.views.decorators.http import require_GET

from catalog.models import Product
from core.seo import canonical_url_for_path


STATIC_SITEMAP_PATHS = (
    '/',
    '/request-parts/',
    '/request-parts/guide/',
    '/request-parts/faq/',
    '/prodavat/',
    '/faq/',
    '/service-request/',
    '/service-request/guide/',
    '/service-request/faq/',
    '/catalog/services/',
    '/parts-sellers/',
)


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
    urls = (
        canonical_url_for_path('/sitemap-static.xml'),
        canonical_url_for_path('/sitemap-products.xml'),
    )
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
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f'{items}'
        '</urlset>'
    )
    return HttpResponse(xml, content_type='application/xml; charset=utf-8')


@require_GET
def sitemap_products(request):
    seen_slugs: set[str] = set()
    items: list[str] = []

    products = Product.objects.filter(status='active').exclude(slug='').order_by('pk').only(
        'slug',
        'updated_at',
    )
    for product in products.iterator():
        if product.slug in seen_slugs:
            continue
        seen_slugs.add(product.slug)
        loc = escape(canonical_url_for_path(f'/{product.slug}/'))
        lastmod = product.updated_at.date().isoformat()
        items.append(f'<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>')

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f'{"".join(items)}'
        '</urlset>'
    )
    return HttpResponse(xml, content_type='application/xml; charset=utf-8')
