"""Suspend public ZPT storefront while retaining the procurement office.

No database rows, uploads, or URL definitions are removed. This middleware is
placed after AuthenticationMiddleware so office access remains authenticated.
Re-enable with PUBLIC_SITE_ENABLED=true when migrating the public offering.
"""
from django.conf import settings
from django.http import HttpResponse


OFFICE_PREFIXES = (
    "/office/", "/admin/", "/control/", "/marketing/",
    "/internal/", "/static/", "/products/",
)
OFFICE_EXACT = {"/office", "/admin", "/control", "/marketing"}
PUBLIC_UTILITY = {"/", "/robots.txt", "/sitemap.xml", "/favicon.ico", "/manifest.json"}


class PublicSiteSuspensionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from core.domain_boundary import is_storefront
        if is_storefront(request):
            return None
        if getattr(settings, "PUBLIC_SITE_ENABLED", False):
            return None

        path = request.path
        if path in PUBLIC_UTILITY or path in OFFICE_EXACT:
            return None
        if path.startswith(OFFICE_PREFIXES):
            return None

        # Staff-only JSON endpoints can still support procurement screens.
        # External buyers and sellers cannot use the public API.
        if path.startswith("/api/"):
            user = request.user
            if user.is_authenticated and user.is_active:
                from core.office_views import office_access
                if office_access(user):
                    return None

        response = HttpResponse(
            "Публичный сервис ZPT.KZ временно приостановлен. "
            "Служебный кабинет: /office/login/",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
        response["Retry-After"] = "86400"
        response["Cache-Control"] = "no-store"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response
