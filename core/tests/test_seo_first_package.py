from types import SimpleNamespace
from pathlib import Path

from django.template import Context, Engine
from django.test import RequestFactory, SimpleTestCase

from core.seo import seo_context
from core.seo_views import STATIC_SITEMAP_PATHS


class FirstSeoPackageTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def context(self, url, name=''):
        request = self.factory.get(url)
        request.resolver_match = SimpleNamespace(url_name=name)
        return seo_context(request)

    def test_plain_pagination_has_own_canonical(self):
        for path, name in (
            ('/parts-sellers/', ''), ('/auto-dismantlers/', ''),
            ('/catalog/services/', ''),
            ('/seller/ag-parts/', 'public_seller_profile'),
            ('/seller/ag-parts/wholesale/', 'public_seller_wholesale'),
        ):
            for page in (1, 2):
                with self.subTest(path=path, page=page):
                    result = self.context(f'{path}?page={page}&utm_source=test', name)
                    self.assertEqual(result['seo_robots'], 'index, follow')
                    self.assertEqual(result['seo_canonical_url'],
                                     f'https://zpt.kz{path}' + ('?page=2' if page == 2 else ''))

    def test_filtered_or_invalid_pagination_stays_excluded(self):
        for query in ('city=Алматы&page=2', 'page=0', 'page=-2',
                      'page=last', 'page=2&page=3', 'page=1000000000'):
            with self.subTest(query=query):
                result = self.context('/parts-sellers/?' + query)
                self.assertEqual(result['seo_robots'], 'noindex, follow')
                self.assertEqual(result['seo_canonical_url'], 'https://zpt.kz/parts-sellers/')

    def test_non_paginated_and_private_routes_do_not_gain_indexing(self):
        for path, directive in (
            ('/parts-sellers/astana/?page=2', 'noindex, follow'),
            ('/sr/token/?page=2', 'noindex, nofollow'),
            ('/market/parts-sellers/?page=2', 'noindex, follow'),
        ):
            self.assertEqual(self.context(path)['seo_robots'], directive)

    def test_car_filters_are_excluded_but_clean_catalogs_remain_indexable(self):
        for path, keys in (
            ('/maintenance-kits/', ('brand', 'model', 'year', 'engine', 'q_brand', 'q_model')),
            ('/our-products/', ('brand', 'model', 'year', 'engine', 'article')),
            ('/ag-parts/filters/', ('brand', 'model', 'year', 'engine', 'article')),
        ):
            self.assertEqual(self.context(path)['seo_robots'], 'index, follow')
            for key in keys:
                with self.subTest(path=path, key=key):
                    self.assertEqual(self.context(path + '?' + key + '=test')['seo_robots'],
                                     'noindex, follow')

    def test_static_sitemap_includes_reviewed_pages_once(self):
        for path in ('/contacts/', '/find-part/', '/seller/ag-parts/wholesale/'):
            self.assertEqual(STATIC_SITEMAP_PATHS.count(path), 1)

    def test_editorial_title_adds_brand_only_if_missing(self):
        source = Path('templates/editorial/detail.html').read_text()
        block = source.split('{% block title %}', 1)[1].split('{% endblock %}', 1)[0]
        template = Engine().from_string(block)
        for title, expected in (
            ('Как подобрать фильтр', 'Как подобрать фильтр — ZPT.KZ'),
            ('Как подобрать фильтр | ZPT.KZ', 'Как подобрать фильтр | ZPT.KZ'),
            ('Как подобрать фильтр | zpt.kz', 'Как подобрать фильтр | zpt.kz'),
        ):
            self.assertEqual(template.render(Context({'article': {'seo_title': title, 'title': 'Материал'}})), expected)
