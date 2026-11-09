from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from catalog.models import SellerProfile, Product
from catalog.commercial import resolve_commercial_price
from .models import Supplier, Offer, Preferences, KaspiSellerSnapshot, RetailListing
from .retail_bulk import publication_candidates, publish_candidate, retail_price, POLICY


class BulkRetailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('bulk-owner', 'test@example.test', 'pass')
        self.seller = SellerProfile.objects.create(user=self.user, slug='ag-parts', name='AG Parts')
        Preferences.objects.create(user=self.user, commission=Decimal('12.5'), expenses=250)
        today = timezone.localdate()
        self.offer = Offer.objects.create(supplier=Supplier.objects.create(name='Фаетон'), brand='555', article='A001',
            name='Шаровая опора 555 A001', cost=2000, stock='10', min_order=2,
            kaspi_price=5000, kaspi_url='https://kaspi.kz/shop/p/a001-123/', match_verified=True,
            supplier_checked_on=today, kaspi_price_checked_on=today, sales_30=3, evidence_date=today)
        KaspiSellerSnapshot.objects.create(offer=self.offer, checked_on=today, complete=True,
            source_url=self.offer.kaspi_url, sellers=[{'price': '5000'}, {'price': '5100'}])
        self.policy = {'pricing_policy': POLICY, 'expenses': '250'}

    def test_publish_price_pack_cart_idempotency_and_privacy(self):
        self.assertEqual(len(publication_candidates(self.user)), 1)
        self.assertEqual(publish_candidate(self.offer.pk, self.user), 'published')
        listing = RetailListing.objects.get(offer=self.offer)
        self.assertEqual((listing.product.price, listing.product.stock_qty), (9500, 5))
        self.assertTrue(resolve_commercial_price(listing.product, 1).can_buy)
        self.assertEqual(publish_candidate(self.offer.pk, self.user), 'published')
        self.assertEqual(Product.objects.count(), 1)
        self.assertNotIn('2000', listing.product.description)
        self.client.force_login(self.user)
        response = self.client.get(reverse('procurement:retail'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '30%')
        self.client.logout()
        response = self.client.get(listing.product.get_absolute_url())
        self.assertContains(response, 'data-cart-add')

    def test_unknown_sales_do_not_become_verified(self):
        self.offer.sales_30 = None
        self.offer.save()
        self.assertEqual(publication_candidates(self.user), [])
        self.assertEqual(publish_candidate(self.offer.pk, self.user), 'skipped')

    def test_stale_mismatched_partial_snapshot_blocks_price(self):
        for changes in ({'complete': False}, {'city': 'wrong'}, {'source_url': 'https://kaspi.kz/shop/p/other/'},
                        {'checked_on': timezone.localdate()-timedelta(days=8)}):
            snapshot = KaspiSellerSnapshot.objects.get(offer=self.offer)
            for key, value in changes.items():
                setattr(snapshot, key, value)
            snapshot.save()
            offer = Offer.objects.get(pk=self.offer.pk)
            self.assertTrue(retail_price(offer, 2, self.policy)[1])
            snapshot.complete = True; snapshot.city = '750000000'
            snapshot.source_url = self.offer.kaspi_url; snapshot.checked_on = timezone.localdate(); snapshot.save()

    def test_profit_floor_and_expired_checkout(self):
        self.offer.cost = 3501
        self.assertTrue(retail_price(self.offer, 1, self.policy)[1])
        self.offer.cost = 3500
        self.assertEqual(retail_price(self.offer, 1, self.policy), (4750, ''))
        self.offer.cost = 2000
        publish_candidate(self.offer.pk, self.user)
        listing = RetailListing.objects.get(offer=self.offer)
        self.offer.supplier_checked_on = timezone.localdate()-timedelta(days=8)
        self.offer.save()
        listing.product.refresh_from_db()
        self.assertEqual(listing.product.status, 'hidden')
        self.assertFalse(resolve_commercial_price(listing.product, 1).can_buy)

    def test_existing_article_is_not_overwritten(self):
        product = Product.objects.create(title='Existing', seller_profile=self.seller, article='A001', price=1234)
        self.assertEqual(publish_candidate(self.offer.pk, self.user), 'duplicate')
        product.refresh_from_db(); self.assertEqual(product.price, 1234)
