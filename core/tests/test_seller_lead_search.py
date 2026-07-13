import io
import json
import logging
from unittest.mock import patch
from urllib import error

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from core.models import SellerLead
from core.services.seller_lead_search import (
    BraveSearchClient,
    SellerLeadSearchConfigError,
    SellerLeadSearchHTTPError,
    SellerLeadSearchTimeoutError,
    build_search_queries,
    collect_instagram_seller_leads,
    normalize_instagram_username,
    parse_instagram_profile_url,
)


class BuildSearchQueriesTests(SimpleTestCase):
    def test_build_query_for_city_and_category(self):
        queries = build_search_queries(city='Алматы', category='автозапчасти')
        self.assertEqual(len(queries), 1)
        query, city, category = queries[0]
        self.assertEqual(query, 'site:instagram.com автозапчасти Алматы WhatsApp')
        self.assertEqual(city, 'Алматы')
        self.assertEqual(category, 'автозапчасти')

    def test_build_query_for_truck_category_uses_kazakhstan(self):
        queries = build_search_queries(city='Астана', category='грузовые запчасти')
        self.assertEqual(queries[0][0], 'site:instagram.com грузовые запчасти Казахстан')

    def test_default_queries_cover_cities_and_categories(self):
        queries = build_search_queries()
        self.assertEqual(len(queries), 5 * 8)


class InstagramProfileParsingTests(SimpleTestCase):
    def test_parse_valid_profile_url(self):
        profile = parse_instagram_profile_url('https://www.instagram.com/example_shop/')
        self.assertEqual(profile['username'], 'example_shop')
        self.assertEqual(profile['profile_url'], 'https://www.instagram.com/example_shop/')

    def test_parse_profile_url_without_www(self):
        profile = parse_instagram_profile_url('https://instagram.com/Example_Shop?igsh=abc')
        self.assertEqual(profile['username'], 'example_shop')

    def test_reject_post_url(self):
        self.assertIsNone(parse_instagram_profile_url('https://www.instagram.com/p/ABC123/'))

    def test_reject_reel_url(self):
        self.assertIsNone(parse_instagram_profile_url('https://www.instagram.com/reel/ABC123/'))

    def test_reject_stories_url(self):
        self.assertIsNone(parse_instagram_profile_url('https://www.instagram.com/stories/example_shop/'))

    def test_normalize_username(self):
        self.assertEqual(normalize_instagram_username('@Shop.Name_1'), 'shop.name_1')
        self.assertEqual(normalize_instagram_username('bad username!'), '')


class BraveSearchClientTests(SimpleTestCase):
    def _mock_response(self, payload: dict, *, status: int = 200):
        body = json.dumps(payload).encode('utf-8')

        class FakeResponse(io.BytesIO):
            status = 200

        return FakeResponse(body)

    def test_search_returns_unified_results(self):
        payload = {
            'web': {
                'results': [
                    {
                        'title': 'Shop title',
                        'url': 'https://www.instagram.com/example_shop/',
                        'description': 'Snippet text',
                    },
                ],
            },
        }

        def fake_urlopen(req, timeout=10):
            return self._mock_response(payload)

        client = BraveSearchClient('secret-key', urlopen=fake_urlopen)
        results = client.search('site:instagram.com автозапчасти Алматы', count=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Shop title')
        self.assertEqual(results[0]['url'], 'https://www.instagram.com/example_shop/')
        self.assertEqual(results[0]['description'], 'Snippet text')

    def test_missing_api_key_raises_config_error(self):
        client = BraveSearchClient('')
        with self.assertRaises(SellerLeadSearchConfigError):
            client.search('query')

    def test_timeout_error(self):
        def fake_urlopen(req, timeout=10):
            raise error.URLError('timed out')

        client = BraveSearchClient('secret-key', urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchTimeoutError):
            client.search('query')

    def test_http_429_error(self):
        def fake_urlopen(req, timeout=10):
            raise error.HTTPError(req.full_url, 429, 'Too Many Requests', hdrs=None, fp=None)

        client = BraveSearchClient('secret-key', urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertEqual(ctx.exception.status_code, 429)

    def test_http_500_error(self):
        def fake_urlopen(req, timeout=10):
            raise error.HTTPError(req.full_url, 500, 'Server Error', hdrs=None, fp=None)

        client = BraveSearchClient('secret-key', urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertEqual(ctx.exception.status_code, 500)

    def test_api_key_not_logged(self):
        payload = {'web': {'results': []}}

        def fake_urlopen(req, timeout=10):
            return self._mock_response(payload)

        client = BraveSearchClient('super-secret-key', urlopen=fake_urlopen)
        with self.assertLogs('core.services.seller_lead_search', level='INFO') as logs:
            client.search('site:instagram.com автозапчасти Алматы')

        joined = '\n'.join(logs.output)
        self.assertIn('site:instagram.com автозапчасти Алматы', joined)
        self.assertNotIn('super-secret-key', joined)


class CollectInstagramSellerLeadsTests(TestCase):
    def _mock_client(self, urls: list[str]):
        payload = {
            'web': {
                'results': [
                    {
                        'title': f'Title {index}',
                        'url': url,
                        'description': f'Description {index}',
                    }
                    for index, url in enumerate(urls)
                ],
            },
        }

        class FakeClient:
            def search(self, query, *, count=10):
                return [
                    {
                        'title': row['title'],
                        'url': row['url'],
                        'description': row['description'],
                    }
                    for row in payload['web']['results']
                ]

        return FakeClient()

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_creates_seller_lead_from_profile(self):
        client = self._mock_client(['https://www.instagram.com/new_shop/'])
        stats = collect_instagram_seller_leads(
            city='Алматы',
            category='автозапчасти',
            limit=5,
            client=client,
        )
        self.assertEqual(stats.created, 1)
        lead = SellerLead.objects.get(instagram_username='new_shop')
        self.assertEqual(lead.city, 'Алматы')
        self.assertEqual(lead.category, 'автозапчасти')
        self.assertEqual(lead.source_type, 'web_search')
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)
        self.assertEqual(lead.instagram_url, 'https://www.instagram.com/new_shop/')

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_skips_existing_username(self):
        SellerLead.objects.create(
            name='Existing',
            instagram_username='existing_shop',
            instagram_url='https://www.instagram.com/existing_shop/',
        )
        client = self._mock_client(['https://www.instagram.com/existing_shop/'])
        stats = collect_instagram_seller_leads(
            city='Алматы',
            category='автозапчасти',
            limit=5,
            client=client,
        )
        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.duplicates_skipped, 1)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_dry_run_does_not_save(self):
        client = self._mock_client(['https://www.instagram.com/dry_run_shop/'])
        stats = collect_instagram_seller_leads(
            city='Алматы',
            category='автозапчасти',
            limit=5,
            dry_run=True,
            client=client,
        )
        self.assertEqual(len(stats.dry_run_profiles), 1)
        self.assertEqual(stats.created, 0)
        self.assertEqual(SellerLead.objects.count(), 0)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_missing_api_key_in_collect_via_client(self):
        with self.assertRaises(SellerLeadSearchConfigError):
            collect_instagram_seller_leads(city='Алматы', category='автозапчасти')

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=False,
    )
    def test_disabled_search_raises_config_error(self):
        with self.assertRaises(SellerLeadSearchConfigError):
            collect_instagram_seller_leads(city='Алматы', category='автозапчасти')


class CollectInstagramSellerLeadsCommandTests(TestCase):
    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_command_requires_api_key(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('collect_instagram_seller_leads', city='Алматы', category='автозапчасти')
        self.assertIn('BRAVE_SEARCH_API_KEY', str(ctx.exception))

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=False,
    )
    def test_command_requires_enabled_flag(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('collect_instagram_seller_leads', city='Алматы', category='автозапчасти')
        self.assertIn('SELLER_SEARCH_ENABLED=False', str(ctx.exception))

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    @patch('core.management.commands.collect_instagram_seller_leads.collect_instagram_seller_leads')
    def test_command_dry_run(self, collect_mock):
        collect_mock.return_value = type('Stats', (), {
            'results_found': 1,
            'profiles_parsed': 1,
            'created': 0,
            'duplicates_skipped': 0,
            'links_rejected': 0,
            'errors': 0,
            'dry_run_profiles': [],
        })()
        call_command(
            'collect_instagram_seller_leads',
            city='Алматы',
            category='автозапчасти',
            limit=10,
            dry_run=True,
        )
        collect_mock.assert_called_once()
        self.assertTrue(collect_mock.call_args.kwargs['dry_run'])
