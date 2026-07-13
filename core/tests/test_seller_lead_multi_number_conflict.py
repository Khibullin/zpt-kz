from __future__ import annotations

import io
import json
import logging
from email.message import Message
from io import StringIO
from unittest.mock import patch
from urllib import error

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import SellerLead, SellerLeadContactCandidate
from core.services.seller_lead_contact_search import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    SearchResultPayload,
    WhatsAppCandidate,
    _select_best_candidate,
    _should_stop_queries_after_current,
    enrich_seller_lead_contacts,
    extract_candidates_from_result,
    upsert_contact_candidate_from_whatsapp,
)
from core.services.seller_lead_search import BraveSearchClient, SellerLeadSearchHTTPError


class MultiNumberConflictRegressionTests(TestCase):
    USERNAME = 'test_subaru_shop'
    SHOP_PHONE = '77011112233'
    SERVICE_PHONE = '77044445566'
    INSTAGRAM_URL = f'https://www.instagram.com/{USERNAME}/'

    def _lead(self, username=USERNAME):
        return SellerLead.objects.create(
            name='Test Subaru Shop',
            instagram_username=username,
            instagram_url=self.INSTAGRAM_URL,
            city='Алматы',
            category='автозапчасти',
            source_type='web_search',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )

    def _snippet_result(self, *, description: str, url: str | None = None):
        return {
            'title': f'Test Subaru Shop (@{self.USERNAME}) • Instagram photos and videos',
            'url': url or self.INSTAGRAM_URL,
            'description': description,
        }

    def _mock_client(self, mapping):
        class FakeClient:
            def search(self, query, count=5):
                return [
                    {
                        'title': row.get('title', ''),
                        'url': row.get('url', ''),
                        'description': row.get('description', ''),
                    }
                    for row in mapping.get(query, [])
                ]

        return FakeClient()

    def _run_enrich(self, *, client, dry_run=False, max_queries_per_lead=3):
        return enrich_seller_lead_contacts(
            username=self.USERNAME,
            max_queries_per_lead=max_queries_per_lead,
            dry_run=dry_run,
            client=client,
        )

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_two_high_numbers_in_one_snippet_create_conflict(self):
        lead = self._lead()
        description = (
            f'Магазин +7 {self.SHOP_PHONE[1:4]} {self.SHOP_PHONE[4:7]} '
            f'{self.SHOP_PHONE[7:9]} {self.SHOP_PHONE[9:11]}. '
            f'Автосервис WhatsApp +7 {self.SERVICE_PHONE[1:4]} {self.SERVICE_PHONE[4:7]} '
            f'{self.SERVICE_PHONE[7:9]} {self.SERVICE_PHONE[9:11]}'
        )
        client = self._mock_client({
            f'site:instagram.com/{self.USERNAME} WhatsApp': [
                self._snippet_result(description=description),
            ],
        })
        stats = self._run_enrich(client=client, dry_run=False, max_queries_per_lead=1)
        lead.refresh_from_db()

        self.assertEqual(stats.conflicts, 1)
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)
        self.assertEqual(lead.contact_candidates.count(), 2)
        self.assertTrue(
            lead.contact_candidates.filter(
                status=SellerLeadContactCandidate.STATUS_CONFLICT,
                is_primary=False,
            ).count()
            == 2,
        )

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_dry_run_does_not_create_conflict_candidates(self):
        lead = self._lead()
        client = self._mock_client({
            f'site:instagram.com/{self.USERNAME} WhatsApp': [
                self._snippet_result(
                    description=(
                        f'Магазин +7 {self.SHOP_PHONE[1:4]} {self.SHOP_PHONE[4:7]} '
                        f'{self.SHOP_PHONE[7:9]} {self.SHOP_PHONE[9:11]}. '
                        f'Сервис WhatsApp +7 {self.SERVICE_PHONE[1:4]} {self.SERVICE_PHONE[4:7]} '
                        f'{self.SERVICE_PHONE[7:9]} {self.SERVICE_PHONE[9:11]}'
                    ),
                ),
            ],
        })
        stats = self._run_enrich(client=client, dry_run=True, max_queries_per_lead=1)
        lead.refresh_from_db()

        self.assertEqual(stats.conflicts, 1)
        self.assertEqual(stats.contact_candidates_created, 0)
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.contact_candidates.count(), 0)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_re_enrichment_does_not_duplicate_conflict_candidates(self):
        lead = self._lead()
        client = self._mock_client({
            f'site:instagram.com/{self.USERNAME} WhatsApp': [
                self._snippet_result(
                    description=(
                        f'Магазин +7 {self.SHOP_PHONE[1:4]} {self.SHOP_PHONE[4:7]} '
                        f'{self.SHOP_PHONE[7:9]} {self.SHOP_PHONE[9:11]}. '
                        f'Сервис WhatsApp +7 {self.SERVICE_PHONE[1:4]} {self.SERVICE_PHONE[4:7]} '
                        f'{self.SERVICE_PHONE[7:9]} {self.SERVICE_PHONE[9:11]}'
                    ),
                ),
            ],
        })
        self._run_enrich(client=client, dry_run=False, max_queries_per_lead=1)
        self._run_enrich(client=client, dry_run=False, max_queries_per_lead=1)
        self.assertEqual(lead.contact_candidates.count(), 2)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_single_high_candidate_still_saved_as_primary(self):
        lead = self._lead()
        client = self._mock_client({
            f'site:instagram.com/{self.USERNAME} WhatsApp': [
                {
                    'title': 'Shop',
                    'url': f'https://wa.me/{self.SHOP_PHONE}',
                    'description': 'WhatsApp Business',
                },
            ],
        })
        stats = self._run_enrich(client=client, dry_run=False, max_queries_per_lead=1)
        lead.refresh_from_db()

        self.assertEqual(stats.saved, 1)
        self.assertEqual(lead.whatsapp, self.SHOP_PHONE)
        self.assertEqual(lead.whatsapp_confidence, CONFIDENCE_HIGH)
        self.assertEqual(lead.contact_candidates.count(), 0)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_single_medium_candidate_still_saved(self):
        lead = self._lead()
        client = self._mock_client({
            f'site:instagram.com/{self.USERNAME} WhatsApp': [
                self._snippet_result(
                    description=f'Контакт {self.USERNAME} +7 {self.SHOP_PHONE[1:4]} {self.SHOP_PHONE[4:]}',
                ),
            ],
        })
        stats = self._run_enrich(client=client, dry_run=False, max_queries_per_lead=1)
        lead.refresh_from_db()

        self.assertEqual(stats.saved, 1)
        self.assertEqual(lead.whatsapp, self.SHOP_PHONE)
        self.assertEqual(lead.whatsapp_confidence, CONFIDENCE_MEDIUM)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_two_numbers_in_different_results_create_conflict(self):
        lead = self._lead()
        client = self._mock_client({
            f'site:instagram.com/{self.USERNAME} WhatsApp': [
                {
                    'title': 'Shop',
                    'url': f'https://wa.me/{self.SHOP_PHONE}',
                    'description': 'WhatsApp Business',
                },
                {
                    'title': 'Service',
                    'url': f'https://wa.me/{self.SERVICE_PHONE}',
                    'description': 'WhatsApp',
                },
            ],
        })
        stats = self._run_enrich(client=client, dry_run=False, max_queries_per_lead=1)
        lead.refresh_from_db()

        self.assertEqual(stats.conflicts, 1)
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.contact_candidates.count(), 2)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_early_stop_waits_for_all_results_in_current_query(self):
        lead = self._lead()
        queries_seen: list[str] = []
        username = self.USERNAME
        shop_phone = self.SHOP_PHONE
        service_phone = self.SERVICE_PHONE

        class CountingClient:
            def search(self, query, count=5):
                queries_seen.append(query)
                if query == f'site:instagram.com/{username} WhatsApp':
                    return [
                        {
                            'title': 'First row',
                            'url': f'https://wa.me/{shop_phone}',
                            'description': 'WhatsApp',
                        },
                        {
                            'title': 'Second row',
                            'url': f'https://wa.me/{service_phone}',
                            'description': 'WhatsApp',
                        },
                    ]
                return []

        stats = self._run_enrich(client=CountingClient(), dry_run=True, max_queries_per_lead=1)
        lead.refresh_from_db()

        self.assertEqual(len(queries_seen), 1)
        self.assertEqual(stats.conflicts, 1)
        self.assertEqual(len(stats.conflict_outcomes), 2)
        self.assertEqual(lead.whatsapp, '')

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_early_stop_allows_next_query_when_only_one_unique_high(self):
        queries_seen: list[str] = []
        username = self.USERNAME
        shop_phone = self.SHOP_PHONE

        class CountingClient:
            def search(self, query, count=5):
                queries_seen.append(query)
                if query == f'site:instagram.com/{username} WhatsApp':
                    return [
                        {
                            'title': 'Only one',
                            'url': f'https://wa.me/{shop_phone}',
                            'description': 'WhatsApp',
                        },
                    ]
                return []

        self._lead()
        self._run_enrich(client=CountingClient(), dry_run=True, max_queries_per_lead=3)
        self.assertEqual(len(queries_seen), 1)

    def test_2gis_catalog_id_not_accepted_as_phone(self):
        lead = self._lead()
        result = SearchResultPayload(
            title='2GIS firm page',
            url='https://2gis.kz/almaty/firm/70000001077782576910',
            description='Справочник организаций',
        )
        candidates = extract_candidates_from_result(result, lead)
        self.assertEqual(candidates, [])

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_conflict_candidate_creation_is_atomic(self):
        lead = self._lead()
        first = WhatsAppCandidate(
            phone=self.SHOP_PHONE,
            confidence=CONFIDENCE_HIGH,
            source_url=self.INSTAGRAM_URL,
            source_text='shop',
        )
        second = WhatsAppCandidate(
            phone=self.SERVICE_PHONE,
            confidence=CONFIDENCE_HIGH,
            source_url=self.INSTAGRAM_URL,
            source_text='service',
        )
        original_create = SellerLeadContactCandidate.objects.create
        calls = {'count': 0}

        def failing_create(*args, **kwargs):
            calls['count'] += 1
            if calls['count'] == 2:
                raise RuntimeError('simulated candidate create failure')
            return original_create(*args, **kwargs)

        with self.assertRaises(RuntimeError):
            with patch.object(SellerLeadContactCandidate.objects, 'create', failing_create):
                with patch(
                    'core.services.seller_lead_contact_search._select_best_candidate',
                    return_value=(None, 'найдено несколько разных номеров с уверенностью high', [first, second]),
                ):
                    enrich_seller_lead_contacts(
                        username=self.USERNAME,
                        max_queries_per_lead=1,
                        dry_run=False,
                        client=self._mock_client({}),
                    )

        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.contact_candidates.count(), 0)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_command_output_reports_conflict_without_saved_label(self):
        self._lead()
        client = self._mock_client({
            f'site:instagram.com/{self.USERNAME} WhatsApp': [
                self._snippet_result(
                    description=(
                        f'Магазин +7 {self.SHOP_PHONE[1:4]} {self.SHOP_PHONE[4:7]} '
                        f'{self.SHOP_PHONE[7:9]} {self.SHOP_PHONE[9:11]}. '
                        f'Сервис WhatsApp +7 {self.SERVICE_PHONE[1:4]} {self.SERVICE_PHONE[4:7]} '
                        f'{self.SERVICE_PHONE[7:9]} {self.SERVICE_PHONE[9:11]}'
                    ),
                ),
            ],
        })
        stdout = StringIO()
        with patch(
            'core.management.commands.enrich_instagram_seller_leads.enrich_seller_lead_contacts',
            return_value=enrich_seller_lead_contacts(
                username=self.USERNAME,
                max_queries_per_lead=1,
                dry_run=True,
                client=client,
            ),
        ):
            call_command(
                'enrich_instagram_seller_leads',
                username=self.USERNAME,
                max_queries_per_lead=1,
                dry_run=True,
                stdout=stdout,
            )
        output = stdout.getvalue()
        self.assertIn('основной WhatsApp не сохранён', output)
        self.assertIn(self.SHOP_PHONE, output)
        self.assertIn(self.SERVICE_PHONE, output)
        self.assertNotIn(f'{self.SHOP_PHONE} | high | сохранён', output)

    def test_api_key_not_logged_or_leaked_in_exceptions(self):
        secret_key = 'BSA-valid-key-0123456789'
        headers = Message()
        headers['Content-Type'] = 'application/json'
        http_error = error.HTTPError(
            'https://api.search.brave.com/res/v1/web/search',
            401,
            'HTTP Error',
            hdrs=headers,
            fp=io.BytesIO(b'{"message":"Unauthorized"}'),
        )

        def fake_urlopen(req, timeout=10):
            raise http_error

        client = BraveSearchClient(secret_key, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertNotIn(secret_key, str(ctx.exception))

        payload = {'web': {'results': []}}

        def ok_urlopen(req, timeout=10):
            body = json.dumps(payload).encode('utf-8')
            headers = Message()
            headers['Content-Type'] = 'application/json'
            response = io.BytesIO(body)
            response.status = 200
            response.headers = headers
            return response

        client = BraveSearchClient(secret_key, urlopen=ok_urlopen)
        with self.assertLogs('core.services.seller_lead_search', level='INFO') as logs:
            client.search('query')
        self.assertNotIn(secret_key, '\n'.join(logs.output))


class SelectBestCandidateUnitTests(TestCase):
    def _lead(self):
        return SellerLead.objects.create(
            name='Unit Lead',
            instagram_username='test_subaru_shop',
            city='Алматы',
            category='автозапчасти',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )

    def test_high_and_medium_from_same_profile_is_conflict(self):
        lead = self._lead()
        instagram_url = 'https://www.instagram.com/test_subaru_shop/'
        candidates = [
            WhatsAppCandidate(
                phone='77011112233',
                confidence=CONFIDENCE_HIGH,
                source_url=instagram_url,
                source_text='shop',
            ),
            WhatsAppCandidate(
                phone='77044445566',
                confidence=CONFIDENCE_MEDIUM,
                source_url=instagram_url,
                source_text='service',
            ),
        ]
        selected, reason, conflicts = _select_best_candidate(candidates, lead=lead)
        self.assertIsNone(selected)
        self.assertTrue(
            reason.startswith('high и medium')
            or reason.startswith('несколько номеров в одном источнике'),
        )
        self.assertEqual(len(conflicts), 2)

    def test_should_not_stop_when_multiple_saveable_numbers_exist(self):
        lead = self._lead()
        candidates = [
            WhatsAppCandidate(
                phone='77011112233',
                confidence=CONFIDENCE_HIGH,
                source_url='https://wa.me/77011112233',
                source_text='a',
            ),
            WhatsAppCandidate(
                phone='77044445566',
                confidence=CONFIDENCE_HIGH,
                source_url='https://wa.me/77044445566',
                source_text='b',
            ),
        ]
        self.assertFalse(_should_stop_queries_after_current(candidates, lead=lead))

    def test_should_stop_when_exactly_one_unique_high_exists(self):
        lead = self._lead()
        candidates = [
            WhatsAppCandidate(
                phone='77011112233',
                confidence=CONFIDENCE_HIGH,
                source_url='https://wa.me/77011112233',
                source_text='a',
            ),
        ]
        self.assertTrue(_should_stop_queries_after_current(candidates, lead=lead))
