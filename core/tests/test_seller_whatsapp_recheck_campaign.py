from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import SellerLead
from core.services.seller_whatsapp_recheck_campaign import (
    CAMPAIGN_STARTED_AT,
    campaign_remaining_count,
    recheck_next_seller_whatsapp,
)


class SellerWhatsappRecheckCampaignTests(TestCase):
    def _lead(self, pk: int, **kwargs):
        defaults = {
            'name': f'Recheck Lead {pk}',
            'city': 'Алматы',
            'source_type': 'web_search',
            'status': SellerLead.STATUS_NEEDS_REVIEW,
            'lifecycle_status': SellerLead.LIFECYCLE_CLASSIFIED,
            'whatsapp': '',
        }
        defaults.update(kwargs)
        return SellerLead.objects.create(pk=pk, **defaults)

    @patch(
        'core.services.seller_whatsapp_recheck_campaign.'
        'active_enrichment_sources',
        return_value=['website', 'brave'],
    )
    @patch(
        'core.services.seller_whatsapp_recheck_campaign.'
        'enrich_seller_lead_contacts',
    )
    def test_rechecks_fixed_campaign_lead_once(
        self,
        enrich,
        active_sources,
    ):
        lead = self._lead(
            1,
            last_enrichment_attempt_at=CAMPAIGN_STARTED_AT - timedelta(days=1),
        )

        def enrich_side_effect(target, **kwargs):
            SellerLead.objects.filter(pk=target.pk).update(
                whatsapp='77015550001',
            )
            return SimpleNamespace(outcome='enriched')

        enrich.side_effect = enrich_side_effect

        result = recheck_next_seller_whatsapp()

        self.assertEqual(result.lead_id, lead.pk)
        self.assertEqual(result.outcome, 'enriched')
        self.assertTrue(result.found_whatsapp)
        self.assertEqual(result.remaining, 0)
        active_sources.assert_called_once()
        enrich.assert_called_once()

        second = recheck_next_seller_whatsapp()
        self.assertEqual(second.outcome, 'complete')
        self.assertIsNone(second.lead_id)
        self.assertEqual(campaign_remaining_count(), 0)

    @patch(
        'core.services.seller_whatsapp_recheck_campaign.'
        'active_enrichment_sources',
        return_value=['website', 'brave'],
    )
    @patch(
        'core.services.seller_whatsapp_recheck_campaign.'
        'enrich_seller_lead_contacts',
    )
    def test_does_not_repeat_lead_checked_after_campaign_start(
        self,
        enrich,
        active_sources,
    ):
        self._lead(
            4,
            last_enrichment_attempt_at=timezone.now(),
        )

        result = recheck_next_seller_whatsapp()

        self.assertEqual(result.outcome, 'complete')
        self.assertIsNone(result.lead_id)
        enrich.assert_not_called()


    @patch(
        'core.services.seller_whatsapp_recheck_campaign.'
        'active_enrichment_sources',
        return_value=['website'],
    )
    @patch(
        'core.services.seller_whatsapp_recheck_campaign.'
        'enrich_seller_lead_contacts',
    )
    def test_requires_brave_before_claiming_any_lead(
        self,
        enrich,
        active_sources,
    ):
        lead = self._lead(
            5,
            last_enrichment_attempt_at=CAMPAIGN_STARTED_AT - timedelta(days=1),
        )

        result = recheck_next_seller_whatsapp()

        self.assertEqual(result.outcome, 'configuration_error')
        self.assertIn('brave', result.error)
        self.assertEqual(result.remaining, 1)
        enrich.assert_not_called()
        lead.refresh_from_db()
        self.assertLess(lead.last_enrichment_attempt_at, CAMPAIGN_STARTED_AT)
