from __future__ import annotations

import io
import json
import logging
from email.message import Message
from unittest.mock import patch
from urllib import error

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings

from core.admin import (
    SellerLeadContactCandidateAdmin,
    approve_contact_candidates_as_primary,
)
from core.models import (
    Seller,
    SellerLead,
    SellerLeadContactCandidate,
    normalize_contact_candidate_value,
)
from core.services.seller_lead_contact_search import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    SOURCE_TEXT_LIMIT,
    enrich_seller_lead_contacts,
    upsert_contact_candidate_from_whatsapp,
    WhatsAppCandidate,
)
from core.services.seller_lead_search import (
    BraveSearchClient,
    SellerLeadSearchHTTPError,
)


class SellerLeadContactCandidateModelTests(TestCase):
    def setUp(self):
        self.lead = SellerLead.objects.create(
            name='Shop Lead',
            instagram_username='shop_lead',
            city='Алматы',
            category='автозапчасти',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )

    def test_create_contact_candidate(self):
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
            source_url='https://www.instagram.com/shop_lead/',
            source_type='instagram_snippet',
        )
        self.assertEqual(candidate.value, '77011234567')
        self.assertEqual(candidate.status, SellerLeadContactCandidate.STATUS_PENDING)

    def test_normalize_contact_value(self):
        self.assertEqual(normalize_contact_candidate_value('+7 701 123 45 67'), '77011234567')
        self.assertEqual(normalize_contact_candidate_value('8 701 123 45 67'), '77011234567')

    def test_duplicate_value_for_same_lead_blocked(self):
        SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
        )
        with self.assertRaises(Exception):
            SellerLeadContactCandidate.objects.create(
                seller_lead=self.lead,
                value='87011234567',
                confidence=CONFIDENCE_HIGH,
            )

    def test_same_value_allowed_for_different_leads(self):
        other = SellerLead.objects.create(
            name='Other Lead',
            instagram_username='other_lead',
            city='Алматы',
            category='автозапчасти',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )
        SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
        )
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=other,
            value='77011234567',
            confidence=CONFIDENCE_MEDIUM,
        )
        self.assertEqual(candidate.value, '77011234567')

    def test_only_one_primary_per_lead(self):
        first = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
            is_primary=True,
            status=SellerLeadContactCandidate.STATUS_APPROVED,
        )
        with self.assertRaises(Exception):
            SellerLeadContactCandidate.objects.create(
                seller_lead=self.lead,
                value='77019876543',
                confidence=CONFIDENCE_HIGH,
                is_primary=True,
                status=SellerLeadContactCandidate.STATUS_APPROVED,
            )
        self.assertTrue(first.is_primary)

    def test_approve_as_primary_updates_seller_lead_whatsapp(self):
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
            source_url='https://www.instagram.com/shop_lead/',
            source_text='wa.me/77011234567',
            source_type='instagram_snippet',
            status=SellerLeadContactCandidate.STATUS_PENDING,
        )
        candidate.approve_as_primary()
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.whatsapp, '77011234567')
        self.assertEqual(self.lead.whatsapp_confidence, CONFIDENCE_HIGH)
        self.assertEqual(self.lead.whatsapp_source_url, candidate.source_url)
        self.assertEqual(self.lead.whatsapp_source_text, candidate.source_text)
        self.assertIsNotNone(self.lead.whatsapp_found_at)

    def test_approve_transfers_evidence_and_keeps_status(self):
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_MEDIUM,
            source_url='https://www.instagram.com/shop_lead/',
            source_text='snippet',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        candidate.approve_as_primary()
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, SellerLead.STATUS_NEEDS_REVIEW)
        self.assertEqual(self.lead.whatsapp_confidence, CONFIDENCE_MEDIUM)

    def test_previous_primary_is_cleared(self):
        first = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
            is_primary=True,
            status=SellerLeadContactCandidate.STATUS_APPROVED,
        )
        first.approve_as_primary()
        second = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77019876543',
            confidence=CONFIDENCE_HIGH,
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        second.approve_as_primary()
        first.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)

    def test_approve_as_primary_is_atomic(self):
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
            source_url='https://www.instagram.com/shop_lead/',
            source_text='snippet',
            status=SellerLeadContactCandidate.STATUS_PENDING,
        )
        original_save = SellerLead.save

        def failing_save(instance, *args, **kwargs):
            if instance.pk == self.lead.pk:
                raise RuntimeError('simulated lead save failure')
            return original_save(instance, *args, **kwargs)

        with self.assertRaises(RuntimeError):
            with patch.object(SellerLead, 'save', failing_save):
                candidate.approve_as_primary()

        candidate.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertEqual(candidate.status, SellerLeadContactCandidate.STATUS_PENDING)
        self.assertFalse(candidate.is_primary)
        self.assertIsNone(candidate.reviewed_at)
        self.assertEqual(self.lead.whatsapp, '')

    def test_rejected_candidate_cannot_become_primary(self):
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
            status=SellerLeadContactCandidate.STATUS_REJECTED,
        )
        with self.assertRaises(ValueError):
            candidate.approve_as_primary()

    def test_whatsapp_url(self):
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
        )
        self.assertEqual(candidate.get_whatsapp_url(), 'https://wa.me/77011234567')

    def test_source_text_length_limited(self):
        long_text = 'x' * 500
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
            source_text=long_text,
        )
        self.assertLessEqual(len(candidate.source_text), SOURCE_TEXT_LIMIT)


class EnrichContactCandidateIntegrationTests(TestCase):
    def _lead(self, username='conflict_lead'):
        return SellerLead.objects.create(
            name='Conflict Lead',
            instagram_username=username,
            city='Алматы',
            category='автозапчасти',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )

    def _mock_client(self, mapping):
        class FakeClient:
            def search(self, query, count=5):
                rows = mapping.get(query, [])
                return [
                    {
                        'title': row.get('title', ''),
                        'url': row.get('url', ''),
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
    def test_conflict_enrichment_creates_two_candidates(self):
        lead = self._lead()
        client = self._mock_client({
            'site:instagram.com/conflict_lead WhatsApp': [
                {
                    'title': 'Conflict shop',
                    'url': 'https://wa.me/77011234567',
                    'description': 'WhatsApp Business',
                },
                {
                    'title': 'Conflict shop 2',
                    'url': 'https://wa.me/77019876543',
                    'description': 'WhatsApp',
                },
            ],
        })
        stats = enrich_seller_lead_contacts(
            username='conflict_lead',
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        lead.refresh_from_db()
        self.assertEqual(stats.conflicts, 1)
        self.assertEqual(stats.contact_candidates_created, 2)
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.contact_candidates.count(), 2)
        self.assertTrue(
            lead.contact_candidates.filter(status=SellerLeadContactCandidate.STATUS_CONFLICT).count() == 2,
        )

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_conflict_keeps_whatsapp_empty(self):
        lead = self._lead('conflict_empty')
        client = self._mock_client({
            'site:instagram.com/conflict_empty WhatsApp': [
                {'title': 'A', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
                {'title': 'B', 'url': 'https://wa.me/77019876543', 'description': 'WhatsApp'},
            ],
        })
        enrich_seller_lead_contacts(
            username='conflict_empty',
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_re_enrichment_does_not_duplicate_candidates(self):
        lead = self._lead('conflict_dup')
        client = self._mock_client({
            'site:instagram.com/conflict_dup WhatsApp': [
                {'title': 'A', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
                {'title': 'B', 'url': 'https://wa.me/77019876543', 'description': 'WhatsApp'},
            ],
        })
        enrich_seller_lead_contacts(username='conflict_dup', max_queries_per_lead=1, dry_run=False, client=client)
        enrich_seller_lead_contacts(username='conflict_dup', max_queries_per_lead=1, dry_run=False, client=client)
        self.assertEqual(lead.contact_candidates.count(), 2)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_dry_run_does_not_create_candidates(self):
        lead = self._lead('conflict_dry')
        client = self._mock_client({
            'site:instagram.com/conflict_dry WhatsApp': [
                {'title': 'A', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
                {'title': 'B', 'url': 'https://wa.me/77019876543', 'description': 'WhatsApp'},
            ],
        })
        stats = enrich_seller_lead_contacts(
            username='conflict_dry',
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        self.assertEqual(stats.contact_candidates_created, 0)
        self.assertEqual(lead.contact_candidates.count(), 0)

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_global_phone_conflict_detected(self):
        other = self._lead('existing_whatsapp')
        other.whatsapp = '77011234567'
        other.save(update_fields=['whatsapp', 'updated_at'])
        lead = self._lead('global_conflict')
        client = self._mock_client({
            'site:instagram.com/global_conflict WhatsApp': [
                {'title': 'Used phone', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        stats = enrich_seller_lead_contacts(
            username='global_conflict',
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        self.assertEqual(stats.global_phone_conflicts, 1)
        self.assertEqual(stats.ready_to_save, 0)


class SellerLeadContactCandidateAdminTests(TestCase):
    def setUp(self):
        self.lead = SellerLead.objects.create(
            name='Admin Lead',
            instagram_username='admin_lead',
            city='Алматы',
            category='автозапчасти',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )
        self.candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77011234567',
            confidence=CONFIDENCE_HIGH,
            source_url='https://www.instagram.com/admin_lead/',
            source_type='instagram_snippet',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_superuser(
            username='admin_contact_test',
            email='admin@test.local',
            password='test-pass',
        )

    def _request_with_messages(self):
        request = self.factory.post('/admin/')
        request.user = self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def test_admin_approve_action_works(self):
        request = self._request_with_messages()
        queryset = SellerLeadContactCandidate.objects.filter(pk=self.candidate.pk)
        approve_contact_candidates_as_primary(None, request, queryset)
        self.candidate.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertTrue(self.candidate.is_primary)
        self.assertEqual(self.lead.whatsapp, '77011234567')

    def test_admin_rejects_multiple_candidates_at_once(self):
        second = SellerLeadContactCandidate.objects.create(
            seller_lead=self.lead,
            value='77019876543',
            confidence=CONFIDENCE_HIGH,
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        request = self._request_with_messages()
        queryset = SellerLeadContactCandidate.objects.filter(pk__in=[self.candidate.pk, second.pk])
        approve_contact_candidates_as_primary(None, request, queryset)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.whatsapp, '')

    def test_admin_whatsapp_link(self):
        admin = SellerLeadContactCandidateAdmin(SellerLeadContactCandidate, AdminSite())
        link = str(admin.whatsapp_link(self.candidate))
        self.assertIn('https://wa.me/77011234567', link)


class BraveSearchClientSecurityCandidateTests(TestCase):
    SECRET_KEY = 'BSA-valid-key-0123456789'

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

    def test_http_errors_do_not_leak_api_key(self):
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

        client = BraveSearchClient(self.SECRET_KEY, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertNotIn(self.SECRET_KEY, str(ctx.exception))


class UpsertContactCandidateTests(TestCase):
    def setUp(self):
        self.lead = SellerLead.objects.create(
            name='Upsert Lead',
            instagram_username='upsert_lead',
            city='Алматы',
            category='автозапчасти',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )
        self.candidate = WhatsAppCandidate(
            phone='77011234567',
            confidence=CONFIDENCE_HIGH,
            source_url='https://www.instagram.com/upsert_lead/',
            source_text='wa.me/77011234567',
        )

    def test_upsert_creates_and_updates(self):
        created, updated = upsert_contact_candidate_from_whatsapp(
            self.lead,
            self.candidate,
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        self.assertTrue(created)
        self.assertFalse(updated)
        created, updated = upsert_contact_candidate_from_whatsapp(
            self.lead,
            self.candidate,
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        self.assertFalse(created)
        self.assertTrue(updated)
        self.assertEqual(self.lead.contact_candidates.count(), 1)
