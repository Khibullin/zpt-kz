"""Shared response policy for the closed office, independent of public SEO."""
from django.conf import settings
from django.utils.cache import patch_vary_headers

PRIVATE_PREFIXES = ('/office/', '/admin/', '/control/', '/marketing/', '/internal/')


def is_private_request(request):
    path = request.path_info
    if path.startswith('/static/'):
        return False
    if path.rstrip('/') in {prefix.rstrip('/') for prefix in PRIVATE_PREFIXES} or path.startswith(PRIVATE_PREFIXES):
        return True
    if getattr(settings, 'DOMAIN_BOUNDARY_ENABLED', False):
        from core.domain_boundary import domain_role
        return domain_role(request) == 'office'
    return False


def protect_office_response(response):
    """Private HTML/JSON/documents are never cached; approved images can be."""
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive, nosnippet'
    cache = response.get('Cache-Control', '')
    private_image = (
        getattr(response, 'office_image_cache', False)
        and response.status_code == 200
        and response.get('Content-Type', '').split(';')[0].startswith('image/')
        and 'private' in cache.lower()
    )
    if private_image:
        if response.has_header('Pragma'):
            del response['Pragma']
    else:
        response['Cache-Control'] = 'private, no-store, max-age=0, must-revalidate'
        response['Pragma'] = 'no-cache'
    response.setdefault('Referrer-Policy', 'same-origin')
    patch_vary_headers(response, ('Cookie',))
    response.disable_analytics = True
    return response
