"""Source reuse is fail-closed and cannot create fictitious profitability."""
import uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from catalog.models import Product, ProductKaspiListing
from procurement.models import Supplier, Offer, KaspiSellerSnapshot
from procurement.kaspi_snapshot_bridge import sync_provisional_snapshot
from repricer.models import KaspiCompetitorIngestBatch, KaspiCompetitorOfferSnapshot


class ExactVerifiedSnapshotTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name='Safe snapshot supplier')
        self.offer = Offer.objects.create(
            supplier=self.supplier, brand='FILTER-BRAND', article='FA-01',
            name='FILTER-BRAND filter FA-01', cost=Decimal('1000.00'),
            stock='5', kaspi_url='https://kaspi.kz/shop/p/filter-12345/',
            match_verified=True, kaspi_price=Decimal('2000.00'),
            kaspi_price_checked_on=timezone.localdate()-timezone.timedelta(days=60))
        product = Product.objects.create(
            article='FA-01', title='Filter FA-01', seller_name='AG Parts',
            whatsapp_number='test-number')
        self.listing = ProductKaspiListing.objects.create(
            product=product, master_sku='12345_export',
            public_url='https://kaspi.kz/shop/p/filter-12345/',
            public_url_verified_at=timezone.now())
        self.batch = KaspiCompetitorIngestBatch.objects.create(
            external_batch_id=uuid.uuid4(), listing=self.listing,
            source='office_collector', captured_at=timezone.now(), offer_count=1)
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=self.listing, seller_name='Trusted Seller', seller_code='Seller1',
            price=Decimal('1990.00'), is_available=True,
            source='office_collector', captured_at=self.batch.captured_at)

    def test_shared_evidence_is_incomplete_and_does_not_update_price(self):
        self.assertTrue(sync_provisional_snapshot(self.offer))
        snapshot = KaspiSellerSnapshot.objects.get(offer=self.offer)
        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.sellers[0]['price'], '1990.00')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.kaspi_price, Decimal('2000.00'))
        self.assertEqual(
            self.offer.kaspi_price_checked_on,
            timezone.localdate()-timezone.timedelta(days=60))
        self.assertFalse(sync_provisional_snapshot(self.offer))

    def test_exact_url_and_explicit_identity_are_required(self):
        self.offer.match_verified=False
        self.offer.save(update_fields=['match_verified'])
        self.assertFalse(sync_provisional_snapshot(self.offer))
        self.offer.match_verified=True
        self.offer.kaspi_url='https://kaspi.kz/shop/p/another-99999/'
        self.offer.save(update_fields=['match_verified','kaspi_url'])
        self.assertFalse(sync_provisional_snapshot(self.offer))
        self.assertFalse(KaspiSellerSnapshot.objects.filter(offer=self.offer).exists())

    def test_verified_complete_snapshot_is_never_replaced(self):
        KaspiSellerSnapshot.objects.create(
            offer=self.offer,source_url=self.offer.kaspi_url,
            city='750000000',checked_on=timezone.localdate(),
            complete=True,sellers=[{'price':'1800','name':'Existing','merchant_id':'Other'}])
        self.assertFalse(sync_provisional_snapshot(self.offer))
        self.assertTrue(KaspiSellerSnapshot.objects.get(offer=self.offer).complete)
