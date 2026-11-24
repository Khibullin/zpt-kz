"""Domain isolation for the unified storefront; disabled until cutover."""
from django.conf import settings
from django.http import HttpResponseNotFound, HttpResponseRedirect, HttpResponseGone

STORE_HOSTS = frozenset({"agparts.kz", "www.agparts.kz"})
OFFICE_HOSTS = frozenset({"zpt.kz", "www.zpt.kz"})
PRIVATE_PREFIXES = ("/office/", "/admin/", "/control/", "/marketing/", "/internal/")
PRIVATE_ROOTS = frozenset(p.rstrip("/") for p in PRIVATE_PREFIXES)

def domain_role(request):
    host = request.get_host().split(":", 1)[0].lower().rstrip(".")
    if host in STORE_HOSTS:
        return "store"
    if host in OFFICE_HOSTS:
        return "office"
    return "other"

def private_path(path):
    return path in PRIVATE_ROOTS or path.startswith(PRIVATE_PREFIXES)

def noindex(response):
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "private, no-store"
    return response

class DomainBoundaryMiddleware:
    """First migration guard, not a public-route allowlist.

    Legacy guards must remain enabled until storefront route/data audits pass.
    This guard deliberately does not enable the storefront or change API access.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "DOMAIN_BOUNDARY_ENABLED", False):
            return self.get_response(request)
        role = domain_role(request)
        path = request.path_info
        if role == "store" and private_path(path):
            return noindex(HttpResponseNotFound())
        if role == "office" and path == "/":
            if request.method in ("GET", "HEAD"):
                return noindex(HttpResponseRedirect("/office/login/"))
            return noindex(HttpResponseNotFound())
        response = self.get_response(request)
        if role == "office" and private_path(path):
            return noindex(response)
        return response
