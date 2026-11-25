"""Opt-in, fail-closed host boundary for the shared Django application."""
from django.conf import settings
from django.http import HttpResponseNotFound, HttpResponseRedirect, HttpResponsePermanentRedirect

STORE_HOSTS = frozenset({"agparts.kz", "www.agparts.kz"})
OFFICE_HOSTS = frozenset({"zpt.kz", "www.zpt.kz", "zpt-kz-backend.onrender.com"})
PRIVATE_PREFIXES = ("/office/", "/admin/", "/control/", "/marketing/", "/internal/")
PRIVATE_ROOTS = frozenset(p.rstrip("/") for p in PRIVATE_PREFIXES)

# Resolved view names, rather than URL prefixes, protect both catalog mounts.
PUBLIC_VIEWS = frozenset({
    "store_filter_category",
    *[f"preview_{kind}{lang}" for lang in ("ru", "kk", "zh") for kind in ("", "category_", "product_", "section_")],
    "home", "catalog_list", "product_detail", "product_detail_old",
    "ag_parts_store", "ag_parts_filter_finder", "interface_find_part",
    "consumables_landing", "services_landing", "interface_section",
    "dismantlers_catalog", "services_catalog", "service_seller_detail",
    "parts_sellers_catalog_public", "parts_seller_detail_public",
    "public_seller_profile", "public_seller_wholesale", "public_seller_wholesale_price",
    "maintenance_kit_list", "maintenance_kit_detail", "maintenance_kit_missing_car",
    "seo_oil_filter_landing", "seo_body_parts_landing", "seo_engine_parts_landing",
    "seo_brake_parts_landing", "seo_suspension_parts_landing", "seo_brand_landing",
    "seo_parts_city", "seo_dismantlers_city", "seo_sto_city", "seo_detailing_city",
    "ajax_load_brands", "ajax_load_models", "ajax_load_compatible_models",
    "countries_list", "brands_by_country", "models_by_brand", "vehicle_suggest",
    "part_categories_list", "platform_contacts", "privacy", "faq", "zpt_gid",
    "editorial_index", "editorial_detail", "minimal_robots", "minimal_sitemap",
    "robots_txt", "sitemap_index", "sitemap_static", "sitemap_products",
    "sitemap_products_page", "sitemap_content", "sitemap_organizations",
    "sitemap_organizations_page", "sitemap_kits", "sitemap_cities",
    "pwa_manifest", "pwa_service_worker", "root_favicon", "media",
    "product_whatsapp_redirect", "orders:cart", "orders:cart_count",
    "orders:order_success", "orders:checkout",
})
PUBLIC_ACTIONS = frozenset({
    "orders:cart_add_api", "orders:cart_add", "orders:cart_remove",
    "orders:cart_update_quantity", "orders:checkout", "product_price_inquiry",
    "maintenance_kit_add_to_cart", "feedback",
    "create_home_parts_request", "create_request",
})
LEGACY_STORE_REDIRECTS = {
    '/changan/': '/avtozapchasti/changan/',
    '/chery/': '/avtozapchasti/chery/',
    '/haval/': '/avtozapchasti/haval/',
    '/zeekr/': '/avtozapchasti/zeekr/',
    '/oilfilters/': '/avtozapchasti/maslyanye-filtry/',
    '/product-page/filterscheryexceedjetour/': '/chery-tiggo-7-t151109111/',
    '/product-page/воздушный-фильтр-1109190cr01/': '/changan-uni-k-1109190cr01/',
}
PUBLIC_ALIAS_FUNCTIONS = frozenset({
    "fitment_slug_redirect", "fitment_canonical_alias",
    "legacy_product_redirect", "canonical_product_alias",
})


def enabled():
    return getattr(settings, "DOMAIN_BOUNDARY_ENABLED", False)


def domain_role(request):
    host = request.get_host().split(":", 1)[0].lower().rstrip(".")
    if host in STORE_HOSTS:
        return "store"
    if host in OFFICE_HOSTS:
        return "office"
    return "other"


def is_storefront(request):
    return enabled() and domain_role(request) == "store"


def private_path(path):
    return path in PRIVATE_ROOTS or path.startswith(PRIVATE_PREFIXES)


def noindex(response):
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "private, no-store"
    return response


def public_view(request, view_func):
    match = request.resolver_match
    name = match.view_name if match else ""
    safe = name in PUBLIC_VIEWS or getattr(view_func, "__name__", "") in PUBLIC_ALIAS_FUNCTIONS
    return safe or name in PUBLIC_ACTIONS


class DomainBoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not enabled():
            return self.get_response(request)
        role = domain_role(request)
        path = request.path_info
        if role == "other" or (role == "store" and private_path(path)):
            return noindex(HttpResponseNotFound())
        if role == "office" and path == "/":
            return noindex(HttpResponseRedirect("/office/login/")) if request.method in ("GET", "HEAD") else noindex(HttpResponseNotFound())
        if role == "store" and request.get_host().split(":")[0].lower().rstrip(".") == "www.agparts.kz":
            return HttpResponsePermanentRedirect("https://agparts.kz" + request.get_full_path()) if request.method in ("GET", "HEAD") else noindex(HttpResponseNotFound())
        if role == "store" and request.method in ("GET", "HEAD"):
            legacy_target = LEGACY_STORE_REDIRECTS.get(path.rstrip('/') + '/')
            if legacy_target:
                return HttpResponsePermanentRedirect('https://agparts.kz' + legacy_target)
        if role == "store" and path == "/favicon.ico":
            return HttpResponsePermanentRedirect("/static/agparts/favicon.png")
        response = self.get_response(request)
        if role == "office" and not path.startswith("/static/"):
            from core.office_privacy import protect_office_response
            return protect_office_response(response)
        if role == "store" and response.status_code >= 400:
            return noindex(response)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not enabled():
            return None
        if domain_role(request) == "store":
            if not public_view(request, view_func):
                return noindex(HttpResponseNotFound())
            name = request.resolver_match.view_name
            if request.method not in ("GET", "HEAD", "OPTIONS") and name not in PUBLIC_ACTIONS:
                return noindex(HttpResponseNotFound())
            if name == "interface_section" and view_kwargs.get("section") == "account":
                return noindex(HttpResponseNotFound())
            # Office identity must never unlock wholesale/admin rendering on store.
            from django.contrib.auth.models import AnonymousUser
            request.user = AnonymousUser()
            if "home_request" in request.GET:
                return noindex(HttpResponseNotFound())
            # /market is a duplicate mount; keep a single public address.
            if request.path == "/market/" or request.path.startswith("/market/"):
                if request.method in ("GET", "HEAD"):
                    return HttpResponsePermanentRedirect("https://agparts.kz" + request.get_full_path()[len("/market"):])
            return None
        # Legacy production public switches must not reopen anonymous ZPT APIs.
        if request.path.startswith('/api/'):
            from core.office_views import office_access
            if request.resolver_match.view_name == 'whatsapp_webhook' or office_access(request.user):
                return None
            return noindex(HttpResponseNotFound())
        # Preserve office, integrations and existing authenticated office APIs.
        if private_path(request.path) or request.path in ("/robots.txt", "/sitemap.xml", "/favicon.ico", "/manifest.json") or request.path.startswith(("/products/", "/static/")):
            return None
        if public_view(request, view_func) and request.method in ("GET", "HEAD"):
            # Old ZPT product aliases may overlap old AGParts category URLs.
            # Resolve them before crossing hosts, preserving exact product intent.
            if getattr(view_func, '__name__', '') in PUBLIC_ALIAS_FUNCTIONS:
                slug = view_kwargs.get('new_slug')
                if slug:
                    return HttpResponsePermanentRedirect('https://agparts.kz/' + slug + '/')
            return HttpResponsePermanentRedirect("https://agparts.kz" + request.get_full_path())
        return noindex(HttpResponseNotFound())
