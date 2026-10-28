from __future__ import annotations

from math import ceil
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET

from catalog.fitment_slug_redirects import public_product_slug
from catalog.models import MaintenanceKit, Product
from core.directory_seo import (
    public_parts_organizations,
    public_service_organizations,
    published_city_page_paths,
)
from core.models import EditorialPage
from core.seo import canonical_url_for_path


# Keep the public static sitemap limited to reviewed indexable landing pages.
STATIC_SITEMAP_PATHS = (
    '/',
    '/contacts/',
    '/find-part/',
    '/seller/ag-parts/wholesale/',
    '/request-parts/',
    '/request-parts/guide/',
    '/zpt-gid/',
    '/prodavat/',
    '/seller/ag-parts/',
    '/parts-sellers/',
    '/auto-dismantlers/',
    '/catalog/services/',
    '/sections/repair/',
    '/sections/detailing/',
    '/service-request/',
    '/our-products/',
    '/to-i-rashodniki/',
    '/avtozapchasti/toyota/',
    '/avtozapchasti/hyundai/',
    '/avtozapchasti/jeep/',
    '/avtozapchasti/changan/',
    '/avtozapchasti/chery/',
    '/avtozapchasti/geely/',
    '/avtozapchasti/haval/',
    '/avtozapchasti/jac/',
    '/avtozapchasti/zeekr/',
    '/avtozapchasti/maslyanye-filtry/',
    '/avtozapchasti/kuzov/',
    '/avtozapchasti/dvigatel/',
    '/avtozapchasti/tormoza/',
    '/avtozapchasti/hodovaya-chast/',
    '/maintenance-kits/',
)

MIN_PRODUCT_DESCRIPTION_LENGTH = 40
ORGANIZATIONS_SITEMAP_PAGE_SIZE = 10_000


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
    return public_product_slug(stored_slug)


@require_GET
def robots_txt(request):
    lines = [
        'User-agent: *',
        'Disallow: /admin/',
        'Disallow: /office/procurement/',
        'Disallow: /control/',
        'Disallow: /api/',
        'Disallow: /marketing/',
        'Disallow: /ajax/',
        'Disallow: /catalog/ajax/',
        'Disallow: /market/ajax/',
        'Disallow: /go/',
        'Disallow: /r/',
        'Disallow: /sr/',
        'Disallow: /internal/',
        'Disallow: /my-request/',
        'Disallow: /my-requests/',
        'Disallow: /seller/join/',
        'Disallow: /seller/dashboard/',
        'Disallow: /seller/profile/',
        'Disallow: /service-request/personal/',
        'Disallow: /service-request/result/',
        'Disallow: /request-parts/cabinet/',
        'Disallow: /service-request/cabinet/',
        'Allow: /static/',
        'Allow: /products/',
        '',
        f'Sitemap: {canonical_url_for_path("/sitemap.xml")}',
        '',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')


def organization_sitemap_paths():
    total = public_parts_organizations().count() + public_service_organizations().count()
    if not total:
        return []
    pages = ceil(total / ORGANIZATIONS_SITEMAP_PAGE_SIZE)
    return [
        '/sitemap-organizations.xml' if number == 1
        else f'/sitemap-organizations-{number}.xml'
        for number in range(1, pages + 1)
    ]


@require_GET
def sitemap_index(request):
    urls = [canonical_url_for_path('/sitemap-static.xml')]
    if getattr(settings, 'SEO_PRODUCT_SITEMAP_ENABLED', False):
        urls.append(canonical_url_for_path('/sitemap-products.xml'))

    if getattr(settings, 'SEO_EDITORIAL_ENABLED', False):
        urls.append(canonical_url_for_path('/sitemap-content.xml'))

    urls.extend(canonical_url_for_path(path) for path in organization_sitemap_paths())
    if MaintenanceKit.objects.filter(is_active=True).exclude(slug='').exists():
        urls.append(canonical_url_for_path('/sitemap-kits.xml'))
    if any(published_city_page_paths()):
        urls.append(canonical_url_for_path('/sitemap-cities.xml'))

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


@require_GET
def sitemap_content(request):
    if not getattr(settings, 'SEO_EDITORIAL_ENABLED', False):
        return HttpResponse(_urlset(), content_type='application/xml; charset=utf-8')
    items = []
    published = EditorialPage.objects.filter(status=EditorialPage.STATUS_PUBLISHED)
    if published.exists():
        index_url = escape(canonical_url_for_path('/guide/parts/'))
        items.append(f'<url><loc>{index_url}</loc></url>')
    for page in published.order_by('pk').iterator():
        loc = escape(canonical_url_for_path(f'/guide/parts/{page.slug}/'))
        lastmod = page.updated_at.date().isoformat()
        items.append(f'<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>')
    return HttpResponse(_urlset(''.join(items)), content_type='application/xml; charset=utf-8')



@require_GET
def sitemap_organizations(request, page=1):
    """All public contactable organizations, split into 10,000-URL sitemaps."""
    sellers = public_parts_organizations()
    services = public_service_organizations()
    seller_count = sellers.count()
    service_count = services.count()
    page_count = max(1, ceil((seller_count + service_count) / ORGANIZATIONS_SITEMAP_PAGE_SIZE))
    if page < 1 or page > page_count:
        raise Http404('Organization sitemap page not found')

    start = (page - 1) * ORGANIZATIONS_SITEMAP_PAGE_SIZE
    end = start + ORGANIZATIONS_SITEMAP_PAGE_SIZE
    items = []

    # A mixed seller has one /parts-seller/<id>/ URL, even when visible in
    # both new-parts and dismantler catalogues.
    if start < seller_count:
        seller_ids = sellers.values_list('pk', flat=True)[start:min(end, seller_count)]
        for pk in seller_ids.iterator():
            url = escape(canonical_url_for_path(f'/parts-seller/{pk}/'))
            items.append(f'<url><loc>{url}</loc></url>')

    service_start = max(0, start - seller_count)
    service_end = max(0, end - seller_count)
    if service_start < service_count:
        service_ids = services.values_list('pk', flat=True)[service_start:service_end]
        for pk in service_ids.iterator():
            url = escape(canonical_url_for_path(f'/catalog/services/{pk}/'))
            items.append(f'<url><loc>{url}</loc></url>')

    return HttpResponse(
        _urlset(''.join(items)), content_type='application/xml; charset=utf-8',
    )


@require_GET
def sitemap_kits(request):
    """Every explicitly published maintenance kit; no unpublished drafts."""
    kits = (
        MaintenanceKit.objects.filter(is_active=True)
        .exclude(slug='')
        .order_by('pk')
        .values_list('slug', 'updated_at')
    )
    items = []
    for slug, updated_at in kits.iterator():
        url = escape(canonical_url_for_path(f'/maintenance-kits/{slug}/'))
        lastmod = updated_at.date().isoformat()
        items.append(f'<url><loc>{url}</loc><lastmod>{lastmod}</lastmod></url>')
    return HttpResponse(
        _urlset(''.join(items)), content_type='application/xml; charset=utf-8',
    )


@require_GET
def sitemap_cities(request):
    """Only city pages with at least three published organizations."""
    items = ''.join(
        f'<url><loc>{escape(canonical_url_for_path(path))}</loc></url>'
        for path in published_city_page_paths()
    )
    return HttpResponse(_urlset(items), content_type='application/xml; charset=utf-8')
