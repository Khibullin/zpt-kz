"""Keep expensive faceted catalogue URLs out of aggressive crawler queues.

These query combinations are excluded from indexing, but observed social-media
crawlers repeatedly request thousands of variants. Reject only *identified
crawlers* at the top of the middleware chain, before ORM work; normal visitors
and the internal procurement office must never be restricted.
"""
from django.http import HttpResponse

from core.seo import CATALOG_FILTER_QUERY_KEYS


_CRAWLER_USER_AGENTS = ('bytespider', 'meta-externalagent', 'facebookexternalhit')
_FACET_KEYS = CATALOG_FILTER_QUERY_KEYS - {'page'}


class CrawlerFilterBudgetMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user_agent = request.headers.get('User-Agent', '').casefold()
        if (
            request.path in ('/', '/market/')
            and any(crawler in user_agent for crawler in _CRAWLER_USER_AGENTS)
            and _FACET_KEYS.intersection(request.GET)
        ):
            response = HttpResponse(
                'Filtered catalog URLs are excluded from crawler access. '
                'Use /sitemap.xml.',
                status=403,
                content_type='text/plain; charset=utf-8',
            )
            response['X-Robots-Tag'] = 'noindex, nofollow'
            # Prevent a shared intermediary from ever applying the crawler
            # response to a human visitor with the same faceted URL.
            response['Cache-Control'] = 'private, no-store'
            response['Vary'] = 'User-Agent'
            return response
        return self.get_response(request)
