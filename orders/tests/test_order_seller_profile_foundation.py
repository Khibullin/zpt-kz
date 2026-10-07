import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import Product, SellerProfile
from orders.admin import OrderAdmin
from orders.models import Order


def create_product(**kwargs):
    defaults = {
        'title': 'Merchant product',
        'price': 1500,
        'seller_name': 'Merchant Snapshot',
        'whatsapp_number': '+77770001122',
        'status': 'active',
        'article': 'MERCHANT-1',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def create_seller_profile(username, name, phone):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
    )


class OrderSellerProfileFoundationTests(TestCase):
    def test_order_can_exist_without_seller_profile(self):
        order = Order.objects.create(
            customer_name='Покупатель',
            customer_phone='77001112233',
            seller_name='Legacy Seller',
            seller_whatsapp='77770000001',
            total_price=1000,
            delivery_method=Order.DELIVERY_COURIER,
        )

        order.refresh_from_db()
        self.assertIsNone(order.seller_profile)
        self.assertIsNone(order.seller_profile_id)

    def test_order_can_be_created_with_explicit_seller_profile(self):
        profile = create_seller_profile(
            'merchant-user',
            'Current Merchant Name',
            '77770000002',
        )

        order = Order.objects.create(
            customer_name='Покупатель',
            customer_phone='77001112233',
            seller_profile=profile,
            seller_name='Historical Merchant Name',
            seller_whatsapp='old-phone',
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
        )

        order.refresh_from_db()
        self.assertEqual(order.seller_profile, profile)
        self.assertEqual(order.seller_name, 'Historical Merchant Name')
        self.assertEqual(order.seller_whatsapp, 'old-phone')

    def test_save_does_not_sync_snapshots_from_seller_profile(self):
        profile = create_seller_profile(
            'merchant-sync',
            'Current Merchant Name',
            '77770000003',
        )
        order = Order.objects.create(
            customer_name='Покупатель',
            customer_phone='77001112233',
            seller_profile=profile,
            seller_name='Historical Merchant Name',
            seller_whatsapp='old-phone',
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
        )

        profile.name = 'Renamed Merchant'
        profile.phone = '77779999999'
        profile.save()
        order.save()
        order.refresh_from_db()

        self.assertEqual(order.seller_profile, profile)
        self.assertEqual(order.seller_name, 'Historical Merchant Name')
        self.assertEqual(order.seller_whatsapp, 'old-phone')

    def test_deleting_seller_profile_nulls_fk_and_keeps_snapshots(self):
        profile = create_seller_profile(
            'merchant-delete',
            'Current Merchant Name',
            '77770000004',
        )
        order = Order.objects.create(
            customer_name='Покупатель',
            customer_phone='77001112233',
            seller_profile=profile,
            seller_name='Historical Merchant Name',
            seller_whatsapp='old-phone',
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
        )

        profile.delete()
        order.refresh_from_db()

        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.assertIsNone(order.seller_profile_id)
        self.assertEqual(order.seller_name, 'Historical Merchant Name')
        self.assertEqual(order.seller_whatsapp, 'old-phone')


class OrderSellerProfileCheckoutTests(TestCase):
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

    def test_checkout_keeps_canonical_seller_empty(self):
        product = create_product(
            seller_name='AG Parts',
            whatsapp_number='+7 777 000 00 00',
        )

        response = self._checkout(product)

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.seller_name, 'AG Parts')
        self.assertEqual(order.seller_whatsapp, '+7 777 000 00 00')
        self.assertIsNone(order.seller_profile)

    def test_buyer_seller_profile_is_not_stored_as_merchant(self):
        buyer = User.objects.create_user(
            username='buyer-user',
            password='secret12345',
        )
        buyer_profile = SellerProfile.objects.create(
            user=buyer,
            name='Buyer Market',
            phone='77771112233',
        )
        merchant = create_seller_profile(
            'other-merchant',
            'Other Merchant',
            '77772223344',
        )
        product = create_product(
            seller_name='Other Merchant',
            whatsapp_number='+77772223344',
            seller_profile=merchant,
            article='OTHER-MERCHANT',
        )

        response = self._checkout(product, user=buyer)

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.user, buyer)
        self.assertIsNone(order.seller_profile)
        self.assertNotEqual(order.seller_profile_id, buyer.seller_profile.pk)
        self.assertEqual(buyer.seller_profile, buyer_profile)

    def test_product_seller_profile_is_not_copied_to_order(self):
        merchant = create_seller_profile(
            'product-merchant',
            'Product Merchant',
            '77773334455',
        )
        product = create_product(
            seller_name='Snapshot Name',
            whatsapp_number='+77773334455',
            seller_profile=merchant,
            article='LINKED-PRODUCT',
        )

        response = self._checkout(product)

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.seller_name, 'Snapshot Name')
        self.assertEqual(order.seller_whatsapp, '+77773334455')
        self.assertIsNone(order.seller_profile)
        self.assertEqual(product.seller_profile, merchant)


class OrderSellerProfileAdminTests(TestCase):
    def test_seller_profile_is_visible_and_readonly(self):
        self.assertIn('seller_profile', OrderAdmin.list_display)
        self.assertIn('seller_profile', OrderAdmin.list_filter)
        self.assertIn('seller_profile__name', OrderAdmin.search_fields)
        self.assertIn('seller_profile__phone', OrderAdmin.search_fields)
        self.assertIn('seller_profile', OrderAdmin.readonly_fields)
        self.assertFalse(getattr(OrderAdmin, 'actions', None))
