import io
import json
import logging
from email.message import Message
from unittest.mock import patch
from urllib import error

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Seller, SellerLead
from core.services.seller_lead_contact_search import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    SearchResultPayload,
    build_contact_search_queries,
    determine_whatsapp_confidence,
    enrich_seller_lead_contacts,
    extract_whatsapp_candidates_from_fields,
    normalize_kz_whatsapp_phone,
)
from core.services.seller_lead_search import (
    BraveSearchClient,
    SellerLeadSearchHTTPError,
    SellerLeadSearchTimeoutError,
)


class NormalizeWhatsappPhoneTests(TestCase):
    def test_extract_from_wa_me(self):
        candidates = extract_whatsapp_candidates_from_fields(
            title='',
            description='',
            url='https://wa.me/77011234567',
        )
        self.assertEqual(candidates[0][0], '77011234567')

    def test_extract_from_api_whatsapp(self):
        candidates = extract_whatsapp_candidates_from_fields(
            title='',
            description='',
            url='https://api.whatsapp.com/send?phone=77011234567',
        )
        self.assertEqual(candidates[0][0], '77011234567')

    def test_normalize_plus7(self):
        self.assertEqual(normalize_kz_whatsapp_phone('+7 701 123 45 67'), '77011234567')

    def test_normalize_leading_8(self):
        self.assertEqual(normalize_kz_whatsapp_phone('8 701 123 45 67'), '77011234567')

    def test_normalize_brackets_spaces_dashes(self):
        self.assertEqual(normalize_kz_whatsapp_phone('7 (701) 123-45-67'), '77011234567')

    def test_reject_short_number(self):
        self.assertIsNone(normalize_kz_whatsapp_phone('7701123'))

    def test_reject_long_number(self):
        self.assertIsNone(normalize_kz_whatsapp_phone('770112345678901'))

    def test_reject_repeating_digits(self):
        self.assertIsNone(normalize_kz_whatsapp_phone('77777777777'))


class WhatsappConfidenceTests(TestCase):
    def _lead(self, **kwargs):
        defaults = {
            'name': 'Omega Auto Parts',
            'instagram_username': 'omega_auto_parts',
            'city': 'Алматы',
        }
        defaults.update(kwargs)
        return SellerLead(**defaults)

    def test_high_confidence_for_wa_me(self):
        result = SearchResultPayload(
            title='Omega shop',
            url='https://wa.me/77011234567',
            description='',
        )
        confidence = determine_whatsapp_confidence(
            phone='77011234567',
            source_kind='wa_url',
            result=result,
            lead=self._lead(),
        )
        self.assertEqual(confidence, CONFIDENCE_HIGH)

    def test_high_confidence_near_whatsapp_word(self):
        result = SearchResultPayload(
            title='Omega Auto Parts WhatsApp +7 701 123 45 67',
            url='https://example.com',
            description='',
        )
        candidates = extract_whatsapp_candidates_from_fields(
            title=result.title,
            description=result.description,
            url=result.url,
        )
        confidence = determine_whatsapp_confidence(
            phone=candidates[0][0],
            source_kind=candidates[0][2],
            result=result,
            lead=self._lead(),
        )
        self.assertEqual(confidence, CONFIDENCE_HIGH)

    def test_medium_confidence_for_exact_username(self):
        result = SearchResultPayload(
            title='omega_auto_parts автозапчасти',
            url='https://www.instagram.com/omega_auto_parts/',
            description='Контакт omega_auto_parts',
        )
        candidates = extract_whatsapp_candidates_from_fields(
            title='WhatsApp 87011234567',
            description=result.description,
            url=result.url,
        )
        confidence = determine_whatsapp_confidence(
            phone=candidates[0][0],
            source_kind=candidates[0][2],
            result=result,
            lead=self._lead(),
        )
        self.assertEqual(confidence, CONFIDENCE_MEDIUM)

    def test_low_confidence_for_weak_match(self):
        result = SearchResultPayload(
            title='Общий каталог магазинов',
            url='https://example.com/catalog',
            description='Телефон 87011234567',
        )
        candidates = extract_whatsapp_candidates_from_fields(
            title=result.title,
            description=result.description,
            url=result.url,
        )
        confidence = determine_whatsapp_confidence(
            phone=candidates[0][0],
            source_kind=candidates[0][2],
            result=result,
            lead=self._lead(),
        )
        self.assertEqual(confidence, CONFIDENCE_LOW)


class EnrichSellerLeadContactsTests(TestCase):
    SECRET_KEY = 'BSA-valid-key-0123456789'

    def _lead(self, username='omega_auto_parts'):
        return SellerLead.objects.create(
            name='Omega Auto Parts',
            instagram_username=username,
            instagram_url=f'https://www.instagram.com/{username}/',
            city='Алматы',
            category='автозапчасти',
            source_type='web_search',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )

    def _mock_client(self, payloads_by_query: dict[str, list[dict]]):
        class FakeClient:
            def search(self, query, *, count=10):
                rows = payloads_by_query.get(query, [])
                return [
                    {
                        'title': row['title'],
                        'url': row['url'],
                        'description': row.get('description', ''),
                    }
                    for row in rows
                ]

        return FakeClient()

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_dry_run_does_not_save(self):
        lead = self._lead()
        client = self._mock_client({
            'site:instagram.com/omega_auto_parts WhatsApp': [
                {
                    'title': 'Omega WhatsApp',
                    'url': 'https://wa.me/77011234567',
                    'description': 'WhatsApp Business',
                },
            ],
        })
        stats = enrich_seller_lead_contacts(
            username='omega_auto_parts',
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        lead.refresh_from_db()
        self.assertEqual(stats.ready_to_save, 1)
        self.assertEqual(stats.saved, 0)
        self.assertEqual(lead.whatsapp, '')
        self.assertIsNone(lead.whatsapp_found_at)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_save_high_and_medium_only(self):
        lead = self._lead()
        client = self._mock_client({
            'site:instagram.com/omega_auto_parts WhatsApp': [
                {
                    'title': 'Omega WhatsApp',
                    'url': 'https://wa.me/77011234567',
                    'description': 'WhatsApp Business',
                },
            ],
        })
        stats = enrich_seller_lead_contacts(
            username='omega_auto_parts',
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        lead.refresh_from_db()
        self.assertEqual(stats.saved, 1)
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(lead.whatsapp_confidence, CONFIDENCE_HIGH)
        self.assertTrue(lead.whatsapp_source_url)
        self.assertTrue(lead.whatsapp_source_text)
        self.assertIsNotNone(lead.whatsapp_found_at)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_conflict_multiple_numbers(self):
        lead = self._lead()
        client = self._mock_client({
            'site:instagram.com/omega_auto_parts WhatsApp': [
                {
                    'title': 'Omega WhatsApp',
                    'url': 'https://wa.me/77011234567',
                    'description': 'WhatsApp Business',
                },
                {
                    'title': 'Omega shop 2',
                    'url': 'https://wa.me/77019876543',
                    'description': 'WhatsApp',
                },
            ],
        })
        stats = enrich_seller_lead_contacts(
            username='omega_auto_parts',
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        lead.refresh_from_db()
        self.assertEqual(stats.conflicts, 1)
        self.assertEqual(lead.whatsapp, '')
        self.assertFalse(stats.lead_outcomes[0].accepted)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_duplicate_phone_other_seller_lead(self):
        self._lead('existing_shop')
        SellerLead.objects.filter(instagram_username='existing_shop').update(whatsapp='77011234567')
        lead = self._lead('omega_auto_parts')
        client = self._mock_client({
            'site:instagram.com/omega_auto_parts WhatsApp': [
                {
                    'title': 'Omega WhatsApp',
                    'url': 'https://wa.me/77011234567',
                    'description': 'WhatsApp Business',
                },
            ],
        })
        stats = enrich_seller_lead_contacts(
            username='omega_auto_parts',
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        self.assertEqual(stats.ready_to_save, 0)
        self.assertFalse(stats.lead_outcomes[0].accepted)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_skips_lead_with_existing_whatsapp(self):
        lead = self._lead('omega_auto_parts')
        SellerLead.objects.filter(pk=lead.pk).update(
            whatsapp='77011234567',
            whatsapp_source_url='https://www.instagram.com/omega_auto_parts/',
            whatsapp_source_text='wa.me/77011234567',
            whatsapp_confidence=CONFIDENCE_HIGH,
        )
        lead.refresh_from_db()
        client = self._mock_client({
            'site:instagram.com/omega_auto_parts WhatsApp': [
                {
                    'title': 'Omega WhatsApp',
                    'url': 'https://wa.me/77019876543',
                    'description': 'WhatsApp Business',
                },
            ],
        })
        stats = enrich_seller_lead_contacts(
            username='omega_auto_parts',
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        lead.refresh_from_db()
        self.assertEqual(stats.leads_processed, 0)
        self.assertEqual(stats.queries_executed, 0)
        self.assertEqual(stats.saved, 0)
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(lead.whatsapp_confidence, CONFIDENCE_HIGH)
        self.assertEqual(lead.whatsapp_source_text, 'wa.me/77011234567')
        self.assertEqual(lead.whatsapp_source_url, 'https://www.instagram.com/omega_auto_parts/')

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_duplicate_phone_registered_seller(self):
        Seller.objects.create(name='Registered', whatsapp='77011234567')
        lead = self._lead('omega_auto_parts')
        client = self._mock_client({
            'site:instagram.com/omega_auto_parts WhatsApp': [
                {
                    'title': 'Omega WhatsApp',
                    'url': 'https://wa.me/77011234567',
                    'description': 'WhatsApp Business',
                },
            ],
        })
        stats = enrich_seller_lead_contacts(
            username='omega_auto_parts',
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        self.assertEqual(stats.ready_to_save, 0)
        self.assertFalse(stats.lead_outcomes[0].accepted)

    def test_build_contact_search_queries(self):
        queries = build_contact_search_queries(
            username='omega_auto_parts',
            name='Omega Auto Parts',
            city='Алматы',
        )
        self.assertEqual(queries[0], 'site:instagram.com/omega_auto_parts WhatsApp')
        self.assertIn('"omega_auto_parts" WhatsApp', queries)


class BraveSearchClientSecurityTests(TestCase):
    SECRET_KEY = 'BSA-valid-key-0123456789'

    def _mock_http_error(self, status: int, body: bytes = b''):
        headers = Message()
        headers['Content-Type'] = 'application/json'
        return error.HTTPError(
            'https://api.search.brave.com/res/v1/web/search',
            status,
            'HTTP Error',
            hdrs=headers,
            fp=io.BytesIO(body),
        )

    def test_http_errors_do_not_leak_api_key(self):
        cases = [
            (401, b'{"message":"Unauthorized"}'),
            (403, b'{"message":"Forbidden"}'),
            (422, b'{"message":"Invalid"}'),
            (429, b'{"message":"Too Many Requests"}'),
            (500, b'{"message":"Server Error"}'),
        ]
        for status_code, body in cases:
            def fake_urlopen(req, timeout=10):
                raise self._mock_http_error(status_code, body)

            client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
            with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
                client.search('query')
            self.assertNotIn(self.SECRET_KEY, str(ctx.exception))

    def test_timeout_error(self):
        def fake_urlopen(req, timeout=10):
            raise error.URLError('timed out')

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchTimeoutError):
            client.search('query')

    def test_api_key_not_logged(self):
        payload = {'web': {'results': []}}

        def fake_urlopen(req, timeout=10):
            body = json.dumps(payload).encode('utf-8')
            headers = Message()
            headers['Content-Type'] = 'application/json'
            response = io.BytesIO(body)
            response.status = 200
            response.headers = headers
            return response

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertLogs('core.services.seller_lead_search', level='INFO') as logs:
            client.search('query')
        self.assertNotIn(self.SECRET_KEY, '\n'.join(logs.output))
