"""Enforce the existing faceted-URL crawl exclusion for the observed heavy spider."""
from django.http import HttpResponse
from core.seo import CATALOG_FILTER_QUERY_KEYS

class CrawlerFilterBudgetMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if (request.path in ('/','/market/') and
            'bytespider' in request.headers.get('User-Agent','').casefold() and
            (CATALOG_FILTER_QUERY_KEYS-{'page'}).intersection(request.GET)):
            response=HttpResponse('Filtered catalog URLs are excluded from crawler access. Use /sitemap.xml.',status=403,content_type='text/plain; charset=utf-8')
            response['X-Robots-Tag']='noindex, nofollow'
            return response
        return self.get_response(request)
