import json
import uuid

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, SellerProfile
from orders.constants import DEFAULT_WAREHOUSE_ADDRESS, SESSION_CART_KEY
from orders.email_notifications import format_delivery_block
from orders.models import Order
from orders.seller_utils import (
    get_order_pickup_display_address,
    normalize_seller_whatsapp,
    resolve_pickup_options,
    resolve_seller_profile_from_items,
)
from orders.tests.test_manual_checkout import create_product, ensure_seller_profile_for_product


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_ADMIN_EMAIL='orders-admin@test.local',
    PUBLIC_BASE_URL='https://zpt.kz',
    ZPT_WAREHOUSE_ADDRESS='г. Алматы, ул. Мурат, 94А',
)
class PickupCheckoutResolutionTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _add_to_cart(self, product, quantity=1):
        return self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps({'product_id': product.id, 'quantity': quantity}),
            content_type='application/json',
        )

    def test_resolve_seller_profile_by_whatsapp_suffix(self):
        product = create_product(whatsapp_number='+7 777 123-45-67')
        seller = ensure_seller_profile_for_product(product, address='Адрес А')
        seller.phone = '87771234567'
        seller.save(update_fields=['phone'])
        profile = resolve_seller_profile_from_items([{'product': product, 'quantity': 1}])
        self.assertEqual(profile.pk, seller.pk)

    def test_local_seller_checkout_shows_own_pickup_address(self):
        product = create_product(article='LOC-1', whatsapp_number='+77770000001', seller_name='Seller One')
        ensure_seller_profile_for_product(product, address='г. Алматы, ул. Продавца, 11')
        self._add_to_cart(product)
        response = self.client.get(reverse('orders:checkout'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Адрес самовывоза: г. Алматы, ул. Продавца, 11')
        self.assertContains(response, 'value="pickup"', html=False)

    def test_two_sellers_have_independent_pickup_addresses(self):
        first = create_product(article='S1', whatsapp_number='+77770000011', seller_name='One')
        second = create_product(article='S2', whatsapp_number='+77770000022', seller_name='Two')
        ensure_seller_profile_for_product(first, address='Адрес продавца One')
        ensure_seller_profile_for_product(second, address='Адрес продавца Two')

        self._add_to_cart(first)
        page_one = self.client.get(reverse('orders:checkout'))
        self.assertContains(page_one, 'Адрес продавца One')
        self.assertNotContains(page_one, 'Адрес продавца Two')

        session = self.client.session
        session[SESSION_CART_KEY] = {}
        session.save()
        self._add_to_cart(second)
        page_two = self.client.get(reverse('orders:checkout'))
        self.assertContains(page_two, 'Адрес продавца Two')
        self.assertNotContains(page_two, 'Адрес продавца One')

    def test_pickup_unavailable_hides_pickup_choice(self):
        product = create_product(article='NO-PICKUP', whatsapp_number='+77770000033')
        ensure_seller_profile_for_product(
            product,
            address='Есть адрес',
            pickup_available=False,
        )
        self._add_to_cart(product)
        response = self.client.get(reverse('orders:checkout'))
        self.assertNotContains(response, 'value="pickup"', html=False)
        forged = self.client.post(reverse('orders:checkout'), data={
            'customer_name': 'Иван',
            'customer_phone': '+7 (701) 123-45-67',
            'delivery_method': Order.DELIVERY_PICKUP,
        })
        self.assertEqual(forged.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)

    def test_empty_effective_pickup_hides_pickup(self):
        product = create_product(article='EMPTY-PICKUP', whatsapp_number='+77770000044')
        ensure_seller_profile_for_product(
            product,
            address='',
            pickup_address='',
            pickup_available=True,
            pickup_same_as_store=True,
        )
        options = resolve_pickup_options([{'product': product, 'quantity': 1}])
        self.assertFalse(options['pickup_available'])

    def test_phaeton_uses_zpt_warehouse_address(self):
        product = create_product(
            article='PHAETON-1',
            supplier=Product.SUPPLIER_PHAETON,
            seller_name='Phaeton (ZPT)',
            whatsapp_number='+77713607040',
        )
        options = resolve_pickup_options([{'product': product, 'quantity': 1}])
        self.assertTrue(options['pickup_available'])
        self.assertEqual(options['effective_pickup_address'], 'г. Алматы, ул. Мурат, 94А')
        self.assertIsNone(options['seller_profile'])

        self._add_to_cart(product)
        response = self.client.get(reverse('orders:checkout'))
        self.assertContains(response, 'Адрес самовывоза: г. Алматы, ул. Мурат, 94А')

    def test_phaeton_ignores_seller_profile_with_same_whatsapp(self):
        """Phaeton must not inherit a marketplace SellerProfile pickup address."""
        zpt_phone = '+77713607040'
        user = User.objects.create_user(username='77713607040', password='secret12345')
        SellerProfile.objects.create(
            user=user,
            name='Own ZPT Cabinet',
            phone=zpt_phone,
            address='Чужой кабинетный адрес, не склад Phaeton',
            pickup_address='Чужой кабинетный адрес, не склад Phaeton',
            pickup_available=True,
            pickup_same_as_store=True,
        )
        product = create_product(
            article='PHAETON-COLLISION',
            supplier=Product.SUPPLIER_PHAETON,
            seller_name='Phaeton (ZPT)',
            whatsapp_number=zpt_phone,
        )
        options = resolve_pickup_options([{'product': product, 'quantity': 1}])
        self.assertIsNone(options['seller_profile'])
        self.assertEqual(options['effective_pickup_address'], 'г. Алматы, ул. Мурат, 94А')
        self.assertNotIn('кабинетный', options['effective_pickup_address'])

        self._add_to_cart(product)
        response = self.client.get(reverse('orders:checkout'))
        self.assertContains(response, 'Адрес самовывоза: г. Алматы, ул. Мурат, 94А')
        self.assertNotContains(response, 'кабинетный')

    def test_local_without_seller_profile_no_zpt_warehouse_pickup(self):
        product = create_product(
            article='ORPHAN-1',
            whatsapp_number='+77770000055',
            seller_name='Orphan Seller',
            supplier=Product.SUPPLIER_LOCAL,
        )
        options = resolve_pickup_options([{'product': product, 'quantity': 1}])
        self.assertFalse(options['pickup_available'])
        self.assertEqual(options['effective_pickup_address'], '')
        self._add_to_cart(product)
        response = self.client.get(reverse('orders:checkout'))
        self.assertNotContains(response, 'Мурат')
        self.assertNotContains(response, 'value="pickup"', html=False)

    def test_pickup_order_stores_address_snapshot(self):
        product = create_product(article='SNAP-1', whatsapp_number='+77770000066')
        seller = ensure_seller_profile_for_product(
            product,
            address='Снимок адреса, 7',
        )
        self._add_to_cart(product)
        response = self.client.post(reverse('orders:checkout'), data={
            'customer_name': 'Иван',
            'customer_phone': '+7 (701) 123-45-67',
            'delivery_method': Order.DELIVERY_PICKUP,
        })
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.delivery_address.get('type'), 'pickup')
        self.assertEqual(order.delivery_address.get('address'), 'Снимок адреса, 7')
        self.assertEqual(order.delivery_address.get('seller_profile_id'), seller.pk)

    def test_order_keeps_snapshot_after_seller_moves(self):
        product = create_product(article='MOVE-1', whatsapp_number='+77770000077')
        seller = ensure_seller_profile_for_product(product, address='Старый адрес выдачи')
        self._add_to_cart(product)
        self.client.post(reverse('orders:checkout'), data={
            'customer_name': 'Иван',
            'customer_phone': '+7 (701) 123-45-67',
            'delivery_method': Order.DELIVERY_PICKUP,
        })
        order = Order.objects.get()

        seller.address = 'Новый адрес после переезда'
        seller.pickup_address = 'Новый адрес после переезда'
        seller.save()

        success = self.client.get(reverse(
            'orders:order_success',
            kwargs={'order_id': order.pk, 'access_token': order.access_token},
        ))
        self.assertContains(success, 'Старый адрес выдачи')
        self.assertNotContains(success, 'Новый адрес после переезда')
        self.assertIn('Старый адрес выдачи', format_delivery_block(order))

    def test_legacy_pickup_order_uses_warehouse_fallback(self):
        product = create_product(article='LEGACY-1')
        order = Order.objects.create(
            customer_name='Legacy',
            customer_phone='+77011234567',
            seller_name='AG Parts',
            seller_whatsapp='+77771234567',
            status=Order.STATUS_NEW,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
            delivery_address={'type': 'pickup'},
            access_token=uuid.uuid4(),
        )
        self.assertEqual(
            get_order_pickup_display_address(order),
            'г. Алматы, ул. Мурат, 94А',
        )
        response = self.client.get(reverse(
            'orders:order_success',
            kwargs={'order_id': order.pk, 'access_token': order.access_token},
        ))
        self.assertContains(response, 'г. Алматы, ул. Мурат, 94А')

    def test_default_warehouse_constant_is_murat(self):
        self.assertEqual(DEFAULT_WAREHOUSE_ADDRESS, 'г. Алматы, ул. Мурат, 94А')
        self.assertNotIn('Райымбека', DEFAULT_WAREHOUSE_ADDRESS)
