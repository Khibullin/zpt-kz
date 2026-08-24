from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from catalog.admin import ProductAdmin
from catalog.forms import ProductForm
from catalog.models import (
    Product,
    ProductConsignment,
    ProductImage,
    ProductPriceTier,
    ProductPromotion,
    SellerProfile,
)
from orders.cart import CartManager


def _make_seller(username, name, phone):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
        city='Алматы',
    )


def _make_product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1500,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77771234567',
        'status': 'active',
        'article': '',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class ProductCommercialFieldsTests(TestCase):
    def test_legacy_product_creates_without_new_fields(self):
        product = _make_product(article='LEGACY-1')

        self.assertIsNone(product.cost_price)
        self.assertIsNone(product.stock_qty)
        self.assertIsNone(product.seller_profile_id)
        self.assertEqual(product.price, 1500)
        self.assertEqual(product.supplier, Product.SUPPLIER_LOCAL)
        self.assertEqual(product.seller_name, 'AG Parts')
        self.assertEqual(product.whatsapp_number, '+77771234567')

    def test_empty_article_allows_multiple_products(self):
        first = _make_product(article='', title='Empty A')
        second = _make_product(article='', title='Empty B')

        self.assertEqual(first.article, '')
        self.assertEqual(second.article, '')
        self.assertEqual(
            Product.objects.filter(article='').count(),
            2,
        )

    def test_empty_article_same_seller_is_allowed(self):
        seller = _make_seller('seller-empty', 'Empty Shop', '77770000001')
        _make_product(article='', seller_profile=seller, title='One')
        _make_product(article='', seller_profile=seller, title='Two')

        self.assertEqual(
            Product.objects.filter(seller_profile=seller, article='').count(),
            2,
        )

    def test_stock_qty_rejects_negative_on_full_clean(self):
        product = _make_product(article='STOCK-1')
        product.stock_qty = -1

        with self.assertRaises(ValidationError) as caught:
            product.full_clean()

        self.assertIn('stock_qty', caught.exception.message_dict)

    def test_stock_qty_rejects_negative_on_save(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _make_product(article='STOCK-NEG', stock_qty=-1)

    def test_cost_price_not_exposed_on_seller_form(self):
        self.assertNotIn('cost_price', ProductForm.Meta.fields)
        self.assertNotIn('stock_qty', ProductForm.Meta.fields)
        self.assertNotIn('seller_profile', ProductForm.Meta.fields)


class ProductPriceTierTests(TestCase):
    def test_product_can_have_multiple_tiers(self):
        product = _make_product(article='TIER-1')
        ProductPriceTier.objects.create(product=product, min_qty=10, price=1200)
        ProductPriceTier.objects.create(product=product, min_qty=20, price=1050)
        ProductPriceTier.objects.create(product=product, min_qty=30, price=900)

        qty_values = list(
            product.price_tiers.order_by('min_qty').values_list('min_qty', flat=True)
        )
        self.assertEqual(qty_values, [10, 20, 30])

    def test_duplicate_min_qty_for_same_product_is_rejected(self):
        product = _make_product(article='TIER-DUP')
        ProductPriceTier.objects.create(product=product, min_qty=10, price=1200)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductPriceTier.objects.create(
                    product=product,
                    min_qty=10,
                    price=1100,
                )

    def test_same_min_qty_allowed_on_different_products(self):
        first = _make_product(article='TIER-A')
        second = _make_product(article='TIER-B')
        ProductPriceTier.objects.create(product=first, min_qty=10, price=1200)
        ProductPriceTier.objects.create(product=second, min_qty=10, price=900)

        self.assertEqual(ProductPriceTier.objects.filter(min_qty=10).count(), 2)

    def test_min_qty_must_be_greater_than_zero(self):
        product = _make_product(article='TIER-ZERO')
        tier = ProductPriceTier(product=product, min_qty=0, price=1000)

        with self.assertRaises(ValidationError) as caught:
            tier.full_clean()

        self.assertIn('min_qty', caught.exception.message_dict)


class ProductArticleUniquenessTests(TestCase):
    def test_same_article_allowed_for_different_sellers(self):
        seller_a = _make_seller('seller-a', 'Shop A', '77770000010')
        seller_b = _make_seller('seller-b', 'Shop B', '77770000011')

        _make_product(article='OEM-100', seller_profile=seller_a)
        _make_product(article='OEM-100', seller_profile=seller_b)

        self.assertEqual(Product.objects.filter(article='OEM-100').count(), 2)

    def test_same_article_rejected_for_one_seller(self):
        seller = _make_seller('seller-dup', 'Shop Dup', '77770000012')
        _make_product(article='OEM-200', seller_profile=seller)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _make_product(article='OEM-200', seller_profile=seller)

    def test_same_article_allowed_without_seller_profile(self):
        _make_product(article='OEM-300', title='Marketplace 1')
        _make_product(article='OEM-300', title='Marketplace 2')

        self.assertEqual(
            Product.objects.filter(
                article='OEM-300',
                seller_profile__isnull=True,
            ).count(),
            2,
        )


class ProductPromotionTests(TestCase):
    def test_valid_promotion_saves(self):
        product = _make_product(article='PROMO-1')
        starts = timezone.now()
        ends = starts + timedelta(days=7)
        promo = ProductPromotion.objects.create(
            product=product,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=900,
            starts_at=starts,
            ends_at=ends,
            qty_limit=5,
        )
        promo.full_clean()
        self.assertEqual(product.promotions.count(), 1)

    def test_ends_at_before_starts_at_is_invalid(self):
        product = _make_product(article='PROMO-DATES')
        starts = timezone.now()
        promo = ProductPromotion(
            product=product,
            promotion_type=ProductPromotion.TYPE_PROMO,
            price=800,
            starts_at=starts,
            ends_at=starts - timedelta(hours=1),
        )

        with self.assertRaises(ValidationError) as caught:
            promo.full_clean()

        self.assertIn('ends_at', caught.exception.message_dict)

    def test_open_ended_promotion_is_valid(self):
        product = _make_product(article='PROMO-OPEN')
        promo = ProductPromotion(
            product=product,
            promotion_type=ProductPromotion.TYPE_PROMO,
            price=700,
            starts_at=timezone.now(),
            ends_at=None,
        )
        promo.full_clean()
        promo.save()
        self.assertTrue(promo.pk)


class ProductConsignmentTests(TestCase):
    def test_consignment_stores_terms_without_dispatch(self):
        product = _make_product(article='CONS-1')
        consignment = ProductConsignment.objects.create(
            product=product,
            enabled=True,
            max_qty=20,
            settlement_price=1100,
            term_days=14,
            conditions='Возврат непроданного через 14 дней.',
        )

        stored = product.consignment
        self.assertEqual(stored.pk, consignment.pk)
        self.assertTrue(stored.enabled)
        self.assertEqual(stored.max_qty, 20)
        self.assertEqual(stored.settlement_price, 1100)
        self.assertEqual(stored.term_days, 14)
        self.assertIn('Возврат', stored.conditions)

    def test_second_consignment_for_same_product_is_rejected(self):
        product = _make_product(article='CONS-DUP')
        ProductConsignment.objects.create(
            product=product,
            enabled=False,
            max_qty=1,
            settlement_price=100,
            term_days=1,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductConsignment.objects.create(
                    product=product,
                    enabled=True,
                    max_qty=2,
                    settlement_price=200,
                    term_days=2,
                )


class ProductImageGalleryTests(TestCase):
    def test_sort_order_and_is_primary_do_not_replace_main_image(self):
        product = _make_product(article='IMG-1')
        extra = ProductImage.objects.create(
            product=product,
            image='products/extra.jpg',
            sort_order=2,
            is_primary=True,
        )

        product.refresh_from_db()
        self.assertFalse(product.main_image)
        self.assertEqual(extra.sort_order, 2)
        self.assertTrue(extra.is_primary)


class ProductAdminCommercialTests(TestCase):
    def test_product_admin_has_commercial_inlines(self):
        inline_models = {inline.model for inline in ProductAdmin.inlines}
        self.assertEqual(
            inline_models,
            {
                ProductImage,
                ProductPriceTier,
                ProductPromotion,
                ProductConsignment,
            },
        )


class PhaetonLookupRegressionTests(TestCase):
    def test_phaeton_upsert_still_matches_article_supplier_brand(self):
        first = CartManager.get_or_create_virtual_product({
            'sku': 'PH-100',
            'brand': 'Toyota',
            'price': 1000,
            'name': 'Phaeton filter',
            'supplier': Product.SUPPLIER_PHAETON,
        })
        second = CartManager.get_or_create_virtual_product({
            'sku': 'PH-100',
            'brand': 'Toyota',
            'price': 2000,
            'name': 'Phaeton filter updated',
            'supplier': Product.SUPPLIER_PHAETON,
        })

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            Product.objects.filter(
                article='PH-100',
                supplier=Product.SUPPLIER_PHAETON,
            ).count(),
            1,
        )
        self.assertEqual(second.supplier, Product.SUPPLIER_PHAETON)
        self.assertIsNone(second.seller_profile_id)
        self.assertIsNone(second.cost_price)
        self.assertIsNone(second.stock_qty)

    def test_phaeton_does_not_merge_with_local_same_article(self):
        local = _make_product(
            article='PH-200',
            supplier=Product.SUPPLIER_LOCAL,
            title='Local PH-200',
        )
        virtual = CartManager.get_or_create_virtual_product({
            'sku': 'PH-200',
            'brand': 'Kia',
            'price': 500,
            'name': 'Phaeton PH-200',
            'supplier': Product.SUPPLIER_PHAETON,
        })

        self.assertNotEqual(local.pk, virtual.pk)
        self.assertEqual(Product.objects.filter(article='PH-200').count(), 2)
