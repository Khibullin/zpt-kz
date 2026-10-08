from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import SellerLead


class SellerWhatsappRecheckOpsTests(TestCase):
    TOKEN = 'test-recheck-token'

    def _lead(self, **kwargs):
        defaults = {
            'name': 'Ops Seller',
            'city': 'Алматы',
            'instagram_username': 'ops_seller',
            'instagram_url': 'https://instagram.com/ops_seller/',
            'source_type': 'web_search',
            'business_type': SellerLead.BUSINESS_TYPE_NEW_PARTS,
            'lifecycle_status': SellerLead.LIFECYCLE_CLASSIFIED,
            'whatsapp': '',
            'last_enrichment_attempt_at': timezone.now() - timedelta(days=30),
        }
        defaults.update(kwargs)
        return SellerLead.objects.create(**defaults)

    @override_settings(SELLER_RECHECK_TOKEN=TOKEN)
    def test_requires_secret_token(self):
        response = self.client.post(
            reverse('seller_whatsapp_recheck_batch'),
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(SELLER_RECHECK_TOKEN=TOKEN)
    @patch('core.ops_seller_recheck.enrich_seller_lead_contacts')
    def test_processes_target_and_marks_progress(self, enrich):
        lead = self._lead()

        class Result:
            outcome = 'enriched'
            verified_whatsapp = ['77015550123']
            errors = []

        def side_effect(obj, **kwargs):
            SellerLead.objects.filter(pk=obj.pk).update(
                whatsapp='77015550123',
                last_enrichment_attempt_at=timezone.now(),
                last_enrichment_result='verified_whatsapp',
            )
            return Result()

        enrich.side_effect = side_effect

        response = self.client.post(
            reverse('seller_whatsapp_recheck_batch') + '?limit=1',
            HTTP_X_ZPT_OPS_TOKEN=self.TOKEN,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['before'], 1)
        self.assertEqual(payload['remaining'], 0)
        self.assertEqual(payload['found_in_batch'], 1)
        self.assertTrue(payload['done'])
        self.assertEqual(payload['processed'][0]['id'], lead.pk)

    @override_settings(SELLER_RECHECK_TOKEN=TOKEN)
    @patch('core.ops_seller_recheck.enrich_seller_lead_contacts')
    def test_excludes_existing_whatsapp_and_terminal_leads(self, enrich):
        self._lead(whatsapp='77015550124')
        self._lead(
            instagram_username='terminal_ops',
            lifecycle_status=SellerLead.LIFECYCLE_REJECTED,
        )

        response = self.client.post(
            reverse('seller_whatsapp_recheck_batch'),
            HTTP_X_ZPT_OPS_TOKEN=self.TOKEN,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['before'], 0)
        self.assertTrue(payload['done'])
        enrich.assert_not_called()
