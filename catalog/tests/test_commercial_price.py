from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.commercial import (
    PRICE_PROMO,
    PRICE_RETAIL,
    PRICE_SALE,
    PRICE_WHOLESALE,
    resolve_commercial_price,
)
from catalog.models import (
    Product,
    ProductPriceTier,
    ProductPromotion,
    SellerProfile,
)


COST_PRICE_MARKER = 87654321
RETAIL = 2000


def _seller(username='price-seller'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name='Price Shop',
        phone='77770000901',
        city='Алматы',
    )


def _product(**kwargs):
    defaults = {
        'title': 'Коммерческая цена',
        'price': RETAIL,
        'cost_price': COST_PRICE_MARKER,
        'seller_name': 'Price Shop',
        'whatsapp_number': '+77770000901',
        'status': 'active',
        'slug': 'commercial-price-product',
        'article': 'CP-1',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _tiers(product):
    ProductPriceTier.objects.create(product=product, min_qty=10, price=1200)
    ProductPriceTier.objects.create(product=product, min_qty=20, price=1050)
    ProductPriceTier.objects.create(product=product, min_qty=30, price=900)


class CommercialPriceResolverTests(TestCase):
    def setUp(self):
        self.seller = _seller()
        self.product = _product()
        _tiers(self.product)

    def _quote(self, qty, seller=None):
        return resolve_commercial_price(self.product, qty, seller_profile=seller)

    def test_guest_gets_retail(self):
        quote = self._quote(20)
        self.assertTrue(quote.can_buy)
        self.assertEqual(quote.price_type, PRICE_RETAIL)
        self.assertEqual(quote.unit_price, RETAIL)
        self.assertEqual(quote.total_price, RETAIL * 20)

    def test_user_without_seller_profile_gets_retail(self):
        User.objects.create_user(username='plain-price', password='secret12345')
        quote = self._quote(25, seller=None)
        self.assertEqual(quote.price_type, PRICE_RETAIL)
        self.assertEqual(quote.unit_price, RETAIL)

    def test_seller_without_b2b_gets_retail(self):
        bare = _product(
            title='Без опта',
            slug='no-b2b-product',
            article='CP-NONE',
        )
        quote = resolve_commercial_price(bare, 15, seller_profile=self.seller)
        self.assertEqual(quote.price_type, PRICE_RETAIL)
        self.assertEqual(quote.unit_price, RETAIL)

    def test_qty_9_stays_retail(self):
        quote = self._quote(9, seller=self.seller)
        self.assertEqual(quote.price_type, PRICE_RETAIL)
        self.assertEqual(quote.unit_price, RETAIL)

    def test_qty_10_uses_tier_10(self):
        quote = self._quote(10, seller=self.seller)
        self.assertEqual(quote.price_type, PRICE_WHOLESALE)
        self.assertEqual(quote.unit_price, 1200)

    def test_qty_19_uses_tier_10(self):
        quote = self._quote(19, seller=self.seller)
        self.assertEqual(quote.unit_price, 1200)

    def test_qty_20_uses_tier_20(self):
        quote = self._quote(20, seller=self.seller)
        self.assertEqual(quote.unit_price, 1050)

    def test_qty_35_uses_tier_30(self):
        quote = self._quote(35, seller=self.seller)
        self.assertEqual(quote.unit_price, 900)
        self.assertEqual(quote.total_price, 900 * 35)

    def test_inactive_tier_is_ignored(self):
        ProductPriceTier.objects.create(
            product=self.product,
            min_qty=5,
            price=100,
            is_active=False,
        )
        quote = self._quote(5, seller=self.seller)
        self.assertEqual(quote.price_type, PRICE_RETAIL)
        self.assertEqual(quote.unit_price, RETAIL)

    def test_sale_beats_wholesale_when_cheaper(self):
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=900,
            is_active=True,
        )
        quote = self._quote(20, seller=self.seller)
        self.assertEqual(quote.price_type, PRICE_SALE)
        self.assertEqual(quote.unit_price, 900)

    def test_wholesale_beats_promo_when_cheaper(self):
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_PROMO,
            price=1300,
            is_active=True,
        )
        quote = self._quote(20, seller=self.seller)
        self.assertEqual(quote.price_type, PRICE_WHOLESALE)
        self.assertEqual(quote.unit_price, 1050)

    def test_expired_promo_is_ignored(self):
        now = timezone.now()
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_PROMO,
            price=500,
            is_active=True,
            starts_at=now - timedelta(days=5),
            ends_at=now - timedelta(hours=1),
        )
        quote = self._quote(20, seller=self.seller)
        self.assertEqual(quote.unit_price, 1050)
        self.assertNotEqual(quote.price_type, PRICE_PROMO)

    def test_future_promo_is_ignored(self):
        now = timezone.now()
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_PROMO,
            price=500,
            is_active=True,
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=5),
        )
        quote = self._quote(20, seller=self.seller)
        self.assertEqual(quote.unit_price, 1050)

    def test_qty_limit_allows_promotion(self):
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=800,
            is_active=True,
            qty_limit=20,
        )
        self.assertEqual(self._quote(10, seller=self.seller).unit_price, 800)
        self.assertEqual(self._quote(20, seller=self.seller).unit_price, 800)

    def test_qty_over_limit_disables_promotion(self):
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=800,
            is_active=True,
            qty_limit=20,
        )
        quote = self._quote(25, seller=self.seller)
        self.assertEqual(quote.price_type, PRICE_WHOLESALE)
        self.assertEqual(quote.unit_price, 1050)

    def test_price_on_request_with_b2b_is_purchasable(self):
        self.product.price = None
        self.product.price_on_request = True
        self.product.save(update_fields=['price', 'price_on_request'])
        quote = self._quote(10, seller=self.seller)
        self.assertTrue(quote.can_buy)
        self.assertEqual(quote.unit_price, 1200)

    def test_price_on_request_without_b2b_is_not_purchasable(self):
        product = _product(
            title='POR без опта',
            slug='por-no-b2b',
            article='POR-0',
            price=None,
            price_on_request=True,
        )
        quote = resolve_commercial_price(product, 1, seller_profile=self.seller)
        self.assertFalse(quote.can_buy)
        guest = resolve_commercial_price(product, 1, seller_profile=None)
        self.assertFalse(guest.can_buy)

    def test_quantity_zero_forbidden(self):
        quote = self._quote(0, seller=self.seller)
        self.assertFalse(quote.can_buy)

    def test_quantity_negative_forbidden(self):
        quote = self._quote(-3, seller=self.seller)
        self.assertFalse(quote.can_buy)

    def test_quantity_over_stock_forbidden(self):
        self.product.stock_qty = 8
        self.product.save(update_fields=['stock_qty'])
        quote = self._quote(9, seller=self.seller)
        self.assertFalse(quote.can_buy)
        self.assertIn('8', quote.reason)

    def test_stock_none_does_not_limit(self):
        self.product.stock_qty = None
        self.product.save(update_fields=['stock_qty'])
        quote = self._quote(80, seller=self.seller)
        self.assertTrue(quote.can_buy)

    def test_public_payload_hides_cost_price(self):
        payload = self._quote(10, seller=self.seller).to_public_dict()
        self.assertNotIn('cost_price', payload)
        self.assertNotIn(str(COST_PRICE_MARKER), str(payload))


class CommercialPricePreviewTests(TestCase):
    def setUp(self):
        self.seller = _seller('preview-seller')
        self.product = _product(slug='preview-price', article='PV-1')
        _tiers(self.product)

    def test_guest_preview_is_retail_and_hides_cost(self):
        response = self.client.get(
            reverse('commercial_price_preview'),
            {'product_id': self.product.id, 'quantity': 20},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['price_type'], PRICE_RETAIL)
        self.assertEqual(data['unit_price'], RETAIL)
        self.assertNotIn('cost_price', data)
        self.assertNotContains(response, str(COST_PRICE_MARKER))

    def test_seller_preview_uses_wholesale(self):
        self.client.login(username='preview-seller', password='secret12345')
        response = self.client.get(
            reverse('commercial_price_preview'),
            {'product_id': self.product.id, 'quantity': 20},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['price_type'], PRICE_WHOLESALE)
        self.assertEqual(data['unit_price'], 1050)
        self.assertEqual(data['total_price'], 1050 * 20)
        self.assertNotIn('cost_price', data)
