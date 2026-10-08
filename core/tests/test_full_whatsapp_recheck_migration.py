import importlib

from django.test import TestCase
from django.utils import timezone

from core.models import SellerLead, SellerLeadSource


class FullWhatsappRecheckMigrationTests(TestCase):
    def test_existing_same_url_source_is_reused(self):
        lead = SellerLead.objects.create(
            name='Migration seller',
            city='Алматы',
            source_type='web_search',
        )
        existing = SellerLeadSource.objects.create(
            seller_lead=lead,
            source_type='web_search',
            provider='brave',
            source_url='https://autofanat.kz/',
            display_name='Autofanat',
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
            is_active=True,
        )

        migration = importlib.import_module(
            'core.migrations.0060_save_full_whatsapp_recheck'
        )
        resolved = migration._website_source(
            SellerLeadSource,
            lead.pk,
            'https://autofanat.kz/',
            timezone.now(),
        )

        self.assertEqual(resolved.pk, existing.pk)
        self.assertEqual(
            SellerLeadSource.objects.filter(
                seller_lead=lead,
                source_url='https://autofanat.kz/',
            ).count(),
            1,
        )
        resolved.refresh_from_db()
        self.assertEqual(resolved.source_type, 'website')
        self.assertEqual(resolved.provider, 'website')
        self.assertEqual(
            resolved.metadata.get('verified_by'),
            'full_whatsapp_recheck_2026-10-08',
        )
