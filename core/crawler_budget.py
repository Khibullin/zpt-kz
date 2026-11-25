"""Keep expensive faceted catalogue URLs out of aggressive crawler queues.

These query combinations are excluded from indexing, but observed social-media
crawlers repeatedly request thousands of variants. Reject only *identified
crawlers* at the top of the middleware chain, before ORM work; normal visitors
and the internal procurement office must never be restricted.
"""
from django.http import HttpResponse

from core.seo import CATALOG_FILTER_QUERY_KEYS


_CRAWLER_USER_AGENTS = ('bytespider', 'meta-externalagent', 'facebookexternalhit', 'gptbot')
_FACET_KEYS = CATALOG_FILTER_QUERY_KEYS - {'page'}


class CrawlerFilterBudgetMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Reject malformed pagination before querying public AG Parts products.
        # Automated requests have sent e.g. "1\u00a0818" as the page value.
        if request.path == '/seller/ag-parts/' and 'page' in request.GET:
            values = request.GET.getlist('page')
            if (
                len(values) != 1
                or not values[0].isascii()
                or not values[0].isdecimal()
                or len(values[0]) > 6
                or int(values[0]) < 1
            ):
                response = HttpResponse(
                    'Invalid page number.',
                    status=400,
                    content_type='text/plain; charset=utf-8',
                )
                response['X-Robots-Tag'] = 'noindex, nofollow'
                response['Cache-Control'] = 'private, no-store'
                return response

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
