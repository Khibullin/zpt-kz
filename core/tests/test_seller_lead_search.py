import gzip
import io
import json
import logging
from email.message import Message
from unittest.mock import patch
from urllib import error
from urllib.parse import parse_qs

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from core.models import SellerLead
from core.services.seller_lead_search import (
    BraveSearchClient,
    INVALID_BRAVE_API_KEY_MESSAGE,
    SellerLeadSearchConfigError,
    SellerLeadSearchError,
    SellerLeadSearchHTTPError,
    SellerLeadSearchTimeoutError,
    _sanitize_api_key,
    build_search_queries,
    collect_instagram_seller_leads,
    get_api_key_validation_metadata,
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


class ApiKeyValidationTests(SimpleTestCase):
    VALID_KEY = 'BSA-valid-key-0123456789'

    def test_valid_ascii_key_is_unchanged(self):
        self.assertEqual(_sanitize_api_key(self.VALID_KEY), self.VALID_KEY)

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(_sanitize_api_key(f'  {self.VALID_KEY}  '), self.VALID_KEY)

    def test_surrounding_double_quotes_are_stripped(self):
        self.assertEqual(_sanitize_api_key(f'"{self.VALID_KEY}"'), self.VALID_KEY)

    def test_surrounding_single_quotes_are_stripped(self):
        self.assertEqual(_sanitize_api_key(f"'{self.VALID_KEY}'"), self.VALID_KEY)

    def test_cyrillic_key_is_rejected(self):
        with self.assertRaises(SellerLeadSearchConfigError) as ctx:
            _sanitize_api_key('йцукенгшщзхъфывапролджэячсмитьбюё')
        self.assertEqual(str(ctx.exception), INVALID_BRAVE_API_KEY_MESSAGE)

    def test_internal_space_is_rejected(self):
        with self.assertRaises(SellerLeadSearchConfigError) as ctx:
            _sanitize_api_key('BSA-key with-space')
        self.assertEqual(str(ctx.exception), INVALID_BRAVE_API_KEY_MESSAGE)

    def test_internal_newline_is_rejected(self):
        with self.assertRaises(SellerLeadSearchConfigError) as ctx:
            _sanitize_api_key('BSA-key\nwith-newline')
        self.assertEqual(str(ctx.exception), INVALID_BRAVE_API_KEY_MESSAGE)

    def test_no_russian_keyboard_layout_conversion(self):
        mistyped = 'иьфыз01234567890123456789012'
        with self.assertRaises(SellerLeadSearchConfigError) as ctx:
            _sanitize_api_key(mistyped)
        self.assertEqual(str(ctx.exception), INVALID_BRAVE_API_KEY_MESSAGE)

    def test_api_key_not_in_exception_message(self):
        secret = 'BSA-secret-not-in-error-message-01'
        with self.assertRaises(SellerLeadSearchConfigError) as ctx:
            _sanitize_api_key(f'{secret[:4]} ключ {secret[4:]}')
        self.assertNotIn(secret, str(ctx.exception))

    def test_api_key_not_logged_by_client(self):
        payload = {'web': {'results': []}}

        def fake_urlopen(req, timeout=10):
            class FakeResponse(io.BytesIO):
                status = 200
                headers = Message()

            response = FakeResponse(json.dumps(payload).encode('utf-8'))
            response.headers['Content-Type'] = 'application/json'
            return response

        client = BraveSearchClient(self.VALID_KEY, urlopen=fake_urlopen)
        with self.assertLogs('core.services.seller_lead_search', level='INFO') as logs:
            client.search('site:instagram.com test')
        joined = '\n'.join(logs.output)
        self.assertNotIn(self.VALID_KEY, joined)


class BraveSearchClientTests(SimpleTestCase):
    SECRET_KEY = 'BSA-valid-key-0123456789'

    def _mock_response(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = 'application/json',
        content_encoding: str = '',
    ):
        headers = Message()
        headers['Content-Type'] = content_type
        if content_encoding:
            headers['Content-Encoding'] = content_encoding

        class FakeResponse(io.BytesIO):
            pass

        response = FakeResponse(body)
        response.status = status
        response.headers = headers
        return response

    def _mock_json_response(self, payload: dict, **kwargs):
        return self._mock_response(json.dumps(payload).encode('utf-8'), **kwargs)

    def _mock_http_error(
        self,
        status: int,
        body: bytes = b'',
        *,
        content_type: str = 'application/json',
        content_encoding: str = '',
    ):
        headers = Message()
        headers['Content-Type'] = content_type
        if content_encoding:
            headers['Content-Encoding'] = content_encoding
        return error.HTTPError(
            'https://api.search.brave.com/res/v1/web/search',
            status,
            'HTTP Error',
            hdrs=headers,
            fp=io.BytesIO(body),
        )

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
            return self._mock_json_response(payload)

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        results = client.search('site:instagram.com автозапчасти Алматы', count=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Shop title')
        self.assertEqual(results[0]['url'], 'https://www.instagram.com/example_shop/')
        self.assertEqual(results[0]['description'], 'Snippet text')

    def test_search_uses_expected_request_shape(self):
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=10):
            captured['method'] = req.method
            captured['url'] = req.full_url.split('?', 1)[0]
            captured['headers'] = dict(req.header_items())
            captured['params'] = parse_qs(req.full_url.split('?', 1)[1])
            return self._mock_json_response({'web': {'results': []}})

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        client.search('site:instagram.com автозапчасти Алматы', count=5)

        headers = {name.lower(): value for name, value in captured['headers'].items()}
        self.assertEqual(captured['method'], 'GET')
        self.assertEqual(captured['url'], 'https://api.search.brave.com/res/v1/web/search')
        self.assertTrue(str(captured['url']).isascii())
        self.assertEqual(headers['accept'], 'application/json')
        self.assertEqual(headers['x-subscription-token'], self.SECRET_KEY)
        self.assertNotIn('accept-encoding', headers)
        self.assertEqual(
            captured['params']['q'],
            ['site:instagram.com автозапчасти Алматы'],
        )
        self.assertEqual(captured['params']['count'], ['5'])

    def test_search_url_encodes_non_ascii_query_as_ascii(self):
        captured: dict[str, str] = {}

        def fake_urlopen(req, timeout=10):
            captured['full_url'] = req.full_url
            return self._mock_json_response({'web': {'results': []}})

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        client.search('site:instagram.com автозапчасти Алматы WhatsApp', count=10)
        self.assertTrue(captured['full_url'].isascii())
        self.assertIn('/res/v1/web/search?', captured['full_url'])
        self.assertIn('q=site%3Ainstagram.com', captured['full_url'])

    def test_search_handles_gzip_encoded_json(self):
        payload = {
            'web': {
                'results': [
                    {
                        'title': 'Gzip shop',
                        'url': 'https://www.instagram.com/gzip_shop/',
                        'description': 'gzip',
                    },
                ],
            },
        }
        raw_body = gzip.compress(json.dumps(payload).encode('utf-8'))

        def fake_urlopen(req, timeout=10):
            return self._mock_response(
                raw_body,
                content_type='application/json',
                content_encoding='gzip',
            )

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        results = client.search('query', count=3)
        self.assertEqual(results[0]['url'], 'https://www.instagram.com/gzip_shop/')

    def test_http_200_html_raises_invalid_json_with_preview(self):
        body = b'<html><body>Not JSON</body></html>'

        def fake_urlopen(req, timeout=10):
            return self._mock_response(body, content_type='text/html')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchError) as ctx:
            client.search('query')
        message = str(ctx.exception)
        self.assertIn('invalid JSON', message)
        self.assertIn('status=200', message)
        self.assertIn('content_type=text/html', message)
        self.assertIn('body_preview=', message)
        self.assertNotIn(self.SECRET_KEY, message)

    def test_http_200_empty_body_raises_empty_body_error(self):
        def fake_urlopen(req, timeout=10):
            return self._mock_response(b'', content_type='application/json')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchError) as ctx:
            client.search('query')
        self.assertIn('empty body', str(ctx.exception))
        self.assertIn('status=200', str(ctx.exception))

    def test_http_200_invalid_content_type_raises_invalid_json(self):
        body = b'plain text response'

        def fake_urlopen(req, timeout=10):
            return self._mock_response(body, content_type='text/plain')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchError) as ctx:
            client.search('query')
        self.assertIn('content_type=text/plain', str(ctx.exception))

    def test_http_401_error(self):
        body = json.dumps({'message': 'Unauthorized'}).encode('utf-8')

        def fake_urlopen(req, timeout=10):
            raise self._mock_http_error(401, body)

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn('HTTP 401', str(ctx.exception))
        self.assertNotIn(self.SECRET_KEY, str(ctx.exception))

    def test_http_403_error(self):
        def fake_urlopen(req, timeout=10):
            raise self._mock_http_error(403, b'{"message":"Forbidden"}')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn('HTTP 403', str(ctx.exception))

    def test_http_422_error(self):
        def fake_urlopen(req, timeout=10):
            raise self._mock_http_error(422, b'{"message":"Invalid count"}')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn('HTTP 422', str(ctx.exception))

    def test_missing_api_key_raises_config_error(self):
        with self.assertRaises(SellerLeadSearchConfigError):
            BraveSearchClient('')

    def test_timeout_error(self):
        def fake_urlopen(req, timeout=10):
            raise error.URLError('timed out')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchTimeoutError):
            client.search('query')

    def test_http_429_error(self):
        def fake_urlopen(req, timeout=10):
            raise self._mock_http_error(429, b'{"message":"Too Many Requests"}')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn('rate limit', str(ctx.exception))

    def test_http_500_error(self):
        def fake_urlopen(req, timeout=10):
            raise self._mock_http_error(500, b'{"message":"Server Error"}')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn('HTTP 500', str(ctx.exception))

    def test_api_key_not_logged(self):
        payload = {'web': {'results': []}}

        def fake_urlopen(req, timeout=10):
            return self._mock_json_response(payload)

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertLogs('core.services.seller_lead_search', level='INFO') as logs:
            client.search('site:instagram.com автозапчасти Алматы')

        joined = '\n'.join(logs.output)
        self.assertIn('site:instagram.com автозапчасти Алматы', joined)
        self.assertNotIn(self.SECRET_KEY, joined)

    def test_api_key_not_in_exception_message(self):
        body = b'<html>secret page</html>'

        def fake_urlopen(req, timeout=10):
            return self._mock_response(body, content_type='text/html')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchError) as ctx:
            client.search('query')
        self.assertNotIn(self.SECRET_KEY, str(ctx.exception))


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
            'dry_run_result_details': [],
            'api_response_info': None,
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
