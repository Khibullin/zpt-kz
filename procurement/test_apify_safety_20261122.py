"""Regression tests for the strict, non-publication Apify cost and identity gates."""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .apify_buying_import import import_apify_results
from .apify_price_recheck import import_apify_recheck_results
from .models import (
    KaspiBuyingCandidate, KaspiPriceAudit, Offer, PermanentBuyingItem,
    Preferences, Supplier,
)


class ApifyImportSafetyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='apify-safety-20261122', password='temporary-test-only'
        )
        Preferences.objects.create(
            user=self.user, commission=Decimal('12.50'), expenses=Decimal('250')
        )
        supplier = Supplier.objects.create(name='Omega')
        self.boundary = Offer.objects.create(
            supplier=supplier, brand='SAMPLE', article='PART-2000',
            name='Амортизатор', cost=Decimal('2000'), stock='5'
        )
        self.below = Offer.objects.create(
            supplier=supplier, brand='SAMPLE', article='PART-1999',
            name='Амортизатор', cost=Decimal('1999'), stock='5'
        )

    @staticmethod
    def card(article, product_id):
        return {
            'id': product_id, 'brand': 'SAMPLE',
            'title': f'SAMPLE Амортизатор {article} 1 шт',
            'url': f'https://kaspi.kz/shop/p/example-{product_id}/',
            'inStock': True,
            'offers': [{'sellerName': 'Test seller', 'price': 5000}],
        }

    def test_recheck_accepts_2000_inclusive_and_rejects_below(self):
        rows = [
            self.card('PART-2000', '170000001'),
            self.card('PART-1999', '170000002'),
        ]
        result = import_apify_recheck_results(
            rows, 'test-apify-cost-recheck.json', self.user
        )
        self.assertEqual(result['rejected_unverifiable'], 1)
        self.assertEqual(
            list(KaspiPriceAudit.objects.values_list('offer_id', flat=True)),
            [self.boundary.pk],
        )
        audit = KaspiPriceAudit.objects.get(offer=self.boundary)
        self.assertEqual(audit.identity_status, 'needs_review')
        self.assertEqual(audit.min_price, Decimal('5000'))
        self.assertEqual(audit.expenses_snapshot, Decimal('250'))
        self.assertFalse(PermanentBuyingItem.objects.exists())

    def test_unverified_2000_cost_is_reviewable_but_not_permanent(self):
        payload = [self.card('PART-2000', '170000011'),
                   self.card('PART-1999', '170000012')]
        upload = SimpleUploadedFile(
            'test-apify-original.json', json.dumps(payload).encode('utf-8'),
            content_type='application/json'
        )
        result = import_apify_results(upload, upload.name, self.user)
        self.assertEqual(result['rejected_unverifiable'], 1)
        candidate = KaspiBuyingCandidate.objects.get(product_id='170000011')
        self.assertEqual(candidate.match_status, 'candidate')
        self.assertFalse(
            KaspiBuyingCandidate.objects.filter(product_id='170000012').exists()
        )
        self.assertFalse(PermanentBuyingItem.objects.exists())

    def test_existing_exact_manual_verification_can_enter_immutable_register(self):
        self.boundary.match_verified = True
        self.boundary.kaspi_url = (
            'https://kaspi.kz/shop/p/example-170000021/'
        )
        self.boundary.save(update_fields=['match_verified', 'kaspi_url'])
        payload = [self.card('PART-2000', '170000021')]
        upload = SimpleUploadedFile(
            'test-apify-verified.json', json.dumps(payload).encode('utf-8'),
            content_type='application/json'
        )
        import_apify_results(upload, upload.name, self.user)
        entry = PermanentBuyingItem.objects.get(offer=self.boundary)
        self.assertEqual(entry.identity_status, 'verified')
        self.assertEqual(entry.cost_snapshot, Decimal('2000'))
        self.assertEqual(entry.expenses_snapshot, Decimal('250'))
        self.assertEqual(PermanentBuyingItem.objects.count(), 1)
