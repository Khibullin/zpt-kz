import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, SellerProfile
from catalog.wholesale import resolve_wholesale_owner
from orders.models import Order
from orders.seller_utils import (
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_CONFLICT,
    RESOLUTION_NOT_FOUND,
    RESOLUTION_RESOLVED,
    SOURCE_EXPLICIT,
    SOURCE_WHATSAPP,
    CartSellerConflictError,
    get_product_seller_key,
    get_seller_snapshot_from_items,
    resolve_canonical_merchant_from_items,
    resolve_order_merchant,
    resolve_pickup_options,
    resolve_product_merchant,
    validate_product_for_cart,
)
from orders.tests.test_manual_checkout import create_product


def create_profile(username, name, phone):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(user=user, name=name, phone=phone)


def items(*products):
    return [{'product': product, 'quantity': 1} for product in products]


@override_settings(ZPT_WAREHOUSE_ADDRESS='г. Алматы, ул. Мурат, 94А')
class CanonicalMerchantResolverTests(TestCase):
    def test_explicit_fk_wins_over_lookalike_phone_and_name(self):
        merchant_a = create_profile('merchant-a', 'Merchant A', '77770000001')
        merchant_b = create_profile('merchant-b', 'Merchant B', '77770000002')
        product = create_product(
            article='EXPLICIT-A',
            seller_profile=merchant_a,
            seller_name='Merchant B',
            whatsapp_number='+7 777 000 00 02',
        )

        resolution = resolve_product_merchant(product)

        self.assertEqual(resolution.status, RESOLUTION_RESOLVED)
        self.assertEqual(resolution.source, SOURCE_EXPLICIT)
        self.assertEqual(resolution.profile, merchant_a)
        self.assertNotEqual(resolution.profile, merchant_b)

    def test_unique_normalized_phone_matches_one_profile(self):
        merchant = create_profile('phone-a', 'Phone Merchant', '87770001122')
        product = create_product(
            article='PHONE-A',
            seller_profile=None,
            seller_name='Unrelated Name',
            whatsapp_number='+7 777 000 11 22',
        )

        resolution = resolve_product_merchant(product)

        self.assertEqual(resolution.profile, merchant)
        self.assertEqual(resolution.source, SOURCE_WHATSAPP)

    def test_duplicate_phone_is_ambiguous_and_not_lowest_pk(self):
        first = create_profile('dup-1', 'First', '77770002233')
        second = create_profile('dup-2', 'Second', '+7 777 000 22 33')
        product = create_product(
            article='DUP-PHONE',
            seller_profile=None,
            whatsapp_number='8 777 000 22 33',
        )

        resolution = resolve_product_merchant(product)

        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.status, RESOLUTION_AMBIGUOUS)
        self.assertNotEqual(resolution.profile, first)
        self.assertNotEqual(resolution.profile, second)

    def test_missing_phone_match_is_not_found(self):
        create_profile('other', 'Other', '77770003344')
        product = create_product(
            article='NO-MATCH',
            seller_profile=None,
            whatsapp_number='+77770009999',
        )

        resolution = resolve_product_merchant(product)

        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.status, RESOLUTION_NOT_FOUND)

    def test_two_explicit_profiles_are_a_conflict(self):
        merchant_a = create_profile('cart-a', 'A', '77770004441')
        merchant_b = create_profile('cart-b', 'B', '77770004441')
        product_a = create_product(
            article='CART-A',
            seller_profile=merchant_a,
            whatsapp_number='+77770004441',
        )
        product_b = create_product(
            article='CART-B',
            seller_profile=merchant_b,
            whatsapp_number='+77770004441',
        )

        resolution = resolve_canonical_merchant_from_items(items(product_a, product_b))

        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.status, RESOLUTION_CONFLICT)
        with self.assertRaises(CartSellerConflictError):
            validate_product_for_cart(items(product_a), product_b)

    def test_same_explicit_profile_ignores_whatsapp_formatting(self):
        merchant = create_profile('same', 'Same', '77770005555')
        first = create_product(
            article='SAME-1',
            seller_profile=merchant,
            whatsapp_number='+7 777 000 55 55',
        )
        second = create_product(
            article='SAME-2',
            seller_profile=merchant,
            whatsapp_number='87770005555',
        )

        resolution = resolve_canonical_merchant_from_items(items(first, second))

        self.assertEqual(resolution.profile, merchant)
        self.assertEqual(get_product_seller_key(first), get_product_seller_key(second))
        validate_product_for_cart(items(first), second)

    def test_legacy_product_keeps_a_shared_whatsapp_cart(self):
        merchant = create_profile('cart-legacy', 'Cart Legacy', '77770006666')
        explicit = create_product(
            article='CART-EXPLICIT',
            seller_profile=merchant,
            whatsapp_number='+7 777 000 66 66',
        )
        legacy = create_product(
            article='CART-LEGACY',
            seller_profile=None,
            whatsapp_number='8 777 000 66 66',
        )

        validate_product_for_cart(items(explicit), legacy)
        snapshot = get_seller_snapshot_from_items(items(explicit, legacy))
        self.assertEqual(snapshot['seller_whatsapp'], '+7 777 000 66 66')

    def test_explicit_plus_unique_legacy_phone_resolves_the_same_merchant(self):
        merchant = create_profile('mixed', 'Mixed', '77770006666')
        explicit = create_product(
            article='MIX-EXPLICIT',
            seller_profile=merchant,
            whatsapp_number='+77770006666',
        )
        legacy = create_product(
            article='MIX-LEGACY',
            seller_profile=None,
            whatsapp_number='8 777 000 66 66',
        )

        resolution = resolve_canonical_merchant_from_items(items(explicit, legacy))

        self.assertEqual(resolution.profile, merchant)
        self.assertEqual(resolution.status, RESOLUTION_RESOLVED)

    def test_explicit_plus_ambiguous_legacy_is_not_guessed(self):
        merchant = create_profile('mix-a', 'Mix A', '77770007777')
        create_profile('mix-b', 'Mix B', '77770008888')
        create_profile('mix-c', 'Mix C', '+77770008888')
        explicit = create_product(
            article='MIX-A',
            seller_profile=merchant,
            whatsapp_number='+77770007777',
        )
        legacy = create_product(
            article='MIX-AMB',
            seller_profile=None,
            whatsapp_number='+77770008888',
        )

        resolution = resolve_canonical_merchant_from_items(items(explicit, legacy))

        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.status, RESOLUTION_AMBIGUOUS)

    def test_phaeton_does_not_inherit_a_profile_from_a_shared_phone(self):
        create_profile('warehouse', 'Warehouse Cabinet', '+77713607040')
        product = create_product(
            article='PHAETON-RESOLVE',
            supplier=Product.SUPPLIER_PHAETON,
            seller_name='Phaeton (ZPT)',
            whatsapp_number='+77713607040',
        )

        resolution = resolve_canonical_merchant_from_items(items(product))
        options = resolve_pickup_options(items(product))

        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.status, RESOLUTION_NOT_FOUND)
        self.assertIsNone(options['seller_profile'])
        self.assertEqual(options['effective_pickup_address'], 'г. Алматы, ул. Мурат, 94А')


class CanonicalOrderMerchantTests(TestCase):
    def test_stored_fk_is_returned_without_phone_lookup(self):
        merchant = create_profile('stored', 'Stored', '77770001111')
        other = create_profile('stored-other', 'Stored', '77770002222')
        order = Order.objects.create(
            customer_name='Покупатель',
            customer_phone='77001112233',
            seller_profile=merchant,
            seller_name='Stored',
            seller_whatsapp='+77770002222',
            total_price=1000,
            delivery_method=Order.DELIVERY_COURIER,
        )

        resolution = resolve_order_merchant(order)

        self.assertEqual(resolution.profile, merchant)
        self.assertEqual(resolution.source, SOURCE_EXPLICIT)
        self.assertNotEqual(resolution.profile, other)

    def test_empty_fk_uses_unique_phone(self):
        merchant = create_profile('legacy-phone', 'Legacy Phone', '87770003333')
        order = Order.objects.create(
            customer_name='Покупатель',
            customer_phone='77001112233',
            seller_name='Different',
            seller_whatsapp='+7 777 000 33 33',
            total_price=1000,
            delivery_method=Order.DELIVERY_COURIER,
        )

        resolution = resolve_order_merchant(order)

        self.assertEqual(resolution.profile, merchant)
        self.assertEqual(resolution.source, SOURCE_WHATSAPP)

    def test_ambiguous_phone_does_not_fall_through_to_name(self):
        create_profile('amb-1', 'Unique Legacy Name', '77770004444')
        create_profile('amb-2', 'Someone Else', '87770004444')
        order = Order.objects.create(
            customer_name='Покупатель',
            customer_phone='77001112233',
            seller_name='Unique Legacy Name',
            seller_whatsapp='+77770004444',
            total_price=1000,
            delivery_method=Order.DELIVERY_COURIER,
        )

        resolution = resolve_order_merchant(order)

        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.status, RESOLUTION_AMBIGUOUS)

    def test_unique_exact_name_is_used_only_after_phone_miss(self):
        merchant = create_profile('name-one', 'Exact Merchant', '77770005555')
        order = Order.objects.create(
            customer_name='Покупатель',
            customer_phone='77001112233',
            seller_name='exact merchant',
            seller_whatsapp='+77770009991',
            total_price=1000,
            delivery_method=Order.DELIVERY_COURIER,
        )

        resolution = resolve_order_merchant(order)

        self.assertEqual(resolution.profile, merchant)

    def test_duplicate_name_is_not_the_lowest_pk(self):
        create_profile('name-a', 'Shared Name', '77770006661')
        create_profile('name-b', 'Shared Name', '77770006662')
        order = Order.objects.create(
            customer_name='Покупатель',
            customer_phone='77001112233',
            seller_name='Shared Name',
            seller_whatsapp='',
            total_price=1000,
            delivery_method=Order.DELIVERY_COURIER,
        )

        resolution = resolve_order_merchant(order)

        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.status, RESOLUTION_AMBIGUOUS)


class WholesaleOwnerResolutionTests(TestCase):
    def test_duplicate_seller_name_does_not_pick_lowest_pk(self):
        create_profile('wh-1', 'Wholesale Twin', '77771110001')
        create_profile('wh-2', 'Wholesale Twin', '77771110002')
        product = create_product(
            article='WH-DUP',
            seller_profile=None,
            seller_name='Wholesale Twin',
            whatsapp_number='+77771110001',
        )

        self.assertIsNone(resolve_wholesale_owner(product))

    def test_explicit_fk_is_the_wholesale_owner(self):
        merchant = create_profile('wh-explicit', 'Named Elsewhere', '77771110003')
        create_profile('wh-name', 'Named Elsewhere', '77771110004')
        product = create_product(
            article='WH-EXPLICIT',
            seller_profile=merchant,
            seller_name='Named Elsewhere',
        )

        self.assertEqual(resolve_wholesale_owner(product), merchant)


class CanonicalCheckoutTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _checkout(self, product, user=None):
        if user is not None:
            self.client.force_login(user)
        self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps({'product_id': product.id, 'quantity': 1}),
            content_type='application/json',
        )
        checkout_url = reverse('orders:checkout')
        self.client.get(checkout_url)
        return self.client.post(checkout_url, data={
            'customer_name': 'Иван',
            'customer_phone': '+7 (701) 123-45-67',
            'delivery_method': Order.DELIVERY_COURIER,
            'courier_street': 'Абая',
            'courier_house': '10',
        })

    def test_checkout_stores_explicit_merchant_and_keeps_snapshots(self):
        merchant = create_profile('checkout-a', 'Current Name', '77772220001')
        product = create_product(
            article='CHECKOUT-EXPLICIT',
            seller_profile=merchant,
            seller_name='Historical Name',
            whatsapp_number='+7 777 222 00 09',
        )

        response = self._checkout(product)

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.seller_profile, merchant)
        self.assertEqual(order.seller_name, 'Historical Name')
        self.assertEqual(order.seller_whatsapp, '+7 777 222 00 09')

    def test_checkout_links_a_unique_legacy_phone(self):
        merchant = create_profile('checkout-legacy', 'Legacy Cabinet', '87772220002')
        product = create_product(
            article='CHECKOUT-LEGACY',
            seller_profile=None,
            seller_name='Snapshot Only',
            whatsapp_number='+77772220002',
        )

        response = self._checkout(product)

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.seller_profile, merchant)
        self.assertEqual(order.seller_name, 'Snapshot Only')
        self.assertEqual(order.seller_whatsapp, '+77772220002')

    def test_checkout_leaves_ambiguous_merchant_empty(self):
        create_profile('amb-checkout-1', 'One', '77772220003')
        create_profile('amb-checkout-2', 'Two', '87772220003')
        product = create_product(
            article='CHECKOUT-AMB',
            seller_profile=None,
            seller_name='Snapshot Seller',
            whatsapp_number='+7 777 222 00 03',
        )

        response = self._checkout(product)

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertIsNone(order.seller_profile)
        self.assertEqual(order.seller_name, 'Snapshot Seller')
        self.assertEqual(order.seller_whatsapp, '+7 777 222 00 03')

    def test_buyer_profile_prices_the_cart_and_merchant_owns_the_order(self):
        buyer = User.objects.create_user(username='buyer-checkout', password='secret12345')
        buyer_profile = create_profile('buyer-profile-user', 'Buyer Market', '77773330001')
        buyer_profile.user = buyer
        buyer_profile.save(update_fields=['user'])
        merchant = create_profile('merchant-checkout', 'Merchant Market', '77773330002')
        product = create_product(
            article='BUYER-MERCHANT',
            seller_profile=merchant,
            seller_name='Merchant Snapshot',
            whatsapp_number='+77773330009',
        )

        with patch('orders.views.resolve_commercial_price', wraps=__import__(
            'catalog.commercial', fromlist=['resolve_commercial_price']
        ).resolve_commercial_price) as priced:
            response = self._checkout(product, user=buyer)

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.user, buyer)
        self.assertEqual(order.seller_profile, merchant)
        self.assertNotEqual(order.seller_profile, buyer_profile)
        self.assertTrue(priced.called)
        self.assertEqual(priced.call_args.kwargs['seller_profile'], buyer_profile)
