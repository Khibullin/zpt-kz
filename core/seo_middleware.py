from __future__ import annotations

from core.seo import robots_directive


class SeoRobotsHeaderMiddleware:
    """Mirror noindex policy in X-Robots-Tag for non-template responses too."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        directive = robots_directive(request)
        if directive.startswith('noindex'):
            response['X-Robots-Tag'] = directive
        return response
