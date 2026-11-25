"""Retire public product views without deleting shared product or accounting data.

The guard uses resolved view names: internal office, import, stock and Kaspi
routes remain available, including their product detail screens.
"""
from django.http import HttpResponseGone
from django.conf import settings


PUBLIC_CATALOG_VIEW_NAMES = frozenset({
    "catalog_list", "product_detail", "numeric_product_entry",
    "ag_parts_store", "ag_parts_filter_finder", "brand_landing",
    "category_landing", "oil_filter_landing",
    "maintenance_kit_list", "maintenance_kit_detail",
    "maintenance_kit_missing_car",
    "public_seller_profile", "public_seller_wholesale",
    "public_seller_wholesale_price", "services_catalog",
    "service_seller_detail", "dismantlers_catalog",
})


class MinimalPublicCatalogueMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from core.domain_boundary import is_storefront
        if is_storefront(request):
            return None
        if getattr(settings, 'PUBLIC_CATALOG_ENABLED', False):
            return None
        if request.path.startswith(("/office/", "/admin/", "/control/", "/api/", "/internal/")):
            return None
        if getattr(view_func, "__name__", "") in PUBLIC_CATALOG_VIEW_NAMES:
            response = HttpResponseGone(
                "Публичный каталог ZPT.KZ отключён. "
                "Для служебной работы перейдите на /office/login/.",
                content_type="text/plain; charset=utf-8",
            )
            response["X-Robots-Tag"] = "noindex, nofollow"
            response["Cache-Control"] = "public, max-age=300"
            return response
        return None
