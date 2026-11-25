"""Regression: crawl budget must never hide buying pages or human filters."""
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from core.crawler_budget import CrawlerFilterBudgetMiddleware


class CrawlerFilterBudgetTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = CrawlerFilterBudgetMiddleware(
            lambda request: HttpResponse('ordinary response', status=200)
        )

    def get(self, path, user_agent):
        return self.middleware(self.factory.get(path, HTTP_USER_AGENT=user_agent))

    def test_observed_meta_crawler_cannot_expand_faceted_catalogue(self):
        for user_agent in (
            'Mozilla/5.0 (compatible; meta-externalagent/1.1)',
            'facebookexternalhit/1.1',
            'Bytespider/1.0',
            'GPTBot/1.4',
        ):
            with self.subTest(user_agent=user_agent):
                response = self.get('/?brand=119&model=918&picker=1', user_agent)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
                self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_real_visitor_can_use_the_same_facets(self):
        response = self.get('/?brand=119&model=918&picker=1', 'Mozilla/5.0 Chrome/145')
        self.assertEqual(response.status_code, 200)

    def test_crawlers_can_access_canonical_and_sitemap_urls(self):
        for path in ('/', '/?page=2', '/sitemap.xml'):
            with self.subTest(path=path):
                self.assertEqual(
                    self.get(path, 'meta-externalagent/1.1').status_code, 200,
                )

    def test_internal_buying_pages_are_never_filtered(self):
        response = self.get(
            '/office/procurement/?mode=best&supplier=2&brand=KIA',
            'meta-externalagent/1.1',
        )
        self.assertEqual(response.status_code, 200)
