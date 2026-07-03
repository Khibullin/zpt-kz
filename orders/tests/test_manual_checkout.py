import json
import uuid
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from django.contrib.auth.models import User

from catalog.models import Product
from orders.cart import CartManager
from orders.constants import SESSION_CART_KEY
from orders.email_notifications import (
    build_order_email_body,
    format_delivery_block,
    send_order_admin_email,
)
from orders.models import KaspiTransaction, Order, OrderItem
from orders.seller_utils import normalize_seller_whatsapp


def create_product(**kwargs):
    defaults = {
        'title': 'Test product',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77771234567',
        'status': 'active',
        'article': 'TEST-001',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_ADMIN_EMAIL='orders-admin@test.local',
    PUBLIC_BASE_URL='https://zpt.kz',
)
class ManualCheckoutTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _add_to_cart(self, product, quantity=1):
        return self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps({
                'product_id': product.id,
                'quantity': quantity,
            }),
            content_type='application/json',
        )

    def _checkout_post(self, product=None):
        if product is not None:
            self._add_to_cart(product)
        checkout_url = reverse('orders:checkout')
        self.client.get(checkout_url)
        return self.client.post(checkout_url, data={
            'customer_name': 'Иван',
            'customer_phone': '+7 (701) 123-45-67',
            'delivery_method': Order.DELIVERY_PICKUP,
        })

    def test_first_product_adds_successfully(self):
        product = create_product()
        response = self._add_to_cart(product)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

    def test_second_product_same_seller_adds_successfully(self):
        seller_a = create_product(title='Product A', article='A-1')
        seller_a_copy = create_product(
            title='Product B',
            article='B-1',
            whatsapp_number='8 (777) 123-45-67',
        )
        response = self._add_to_cart(seller_a)
        self.assertEqual(response.status_code, 200)
        response = self._add_to_cart(seller_a_copy)
        self.assertEqual(response.status_code, 200)

    def test_other_seller_product_rejected_with_409(self):
        first = create_product(seller_name='AG Parts', whatsapp_number='+77771234567')
        second = create_product(
            title='Other seller product',
            article='OTHER-1',
            seller_name='Other Seller',
            whatsapp_number='+77009998877',
        )
        self._add_to_cart(first)
        response = self._add_to_cart(second)
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertFalse(payload['success'])
        self.assertIn('другого продавца', payload['error'])

    def test_same_seller_different_phone_formats(self):
        formats = ['+7 777 123 45 67', '8 777 123 45 67', '77771234567']
        first = create_product(article='FMT-1', whatsapp_number=formats[0])
        self._add_to_cart(first)
        for index, phone in enumerate(formats[1:], start=2):
            product = create_product(
                title=f'Product {index}',
                article=f'FMT-{index}',
                whatsapp_number=phone,
            )
            response = self._add_to_cart(product)
            self.assertEqual(response.status_code, 200, msg=phone)

    def test_normalize_seller_whatsapp_formats(self):
        self.assertEqual(
            normalize_seller_whatsapp('+7 777 123 45 67'),
            normalize_seller_whatsapp('87771234567'),
        )
        self.assertEqual(
            normalize_seller_whatsapp('77771234567'),
            '77771234567',
        )

    @patch('integrations.kaspi_pay.KaspiPayClient.create_invoice')
    def test_checkout_creates_order_with_status_new(self, mock_create_invoice):
        product = create_product(price=9280)
        response = self._checkout_post(product)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.status, Order.STATUS_NEW)
        self.assertEqual(order.total_price, 9280)
        mock_create_invoice.assert_not_called()

    def test_checkout_creates_all_order_items(self):
        first = create_product(title='Spark plugs', article='ABC-123', price=9280)
        second = create_product(
            title='Air filter',
            article='AF-500',
            whatsapp_number='87771234567',
            price=4000,
        )
        self._add_to_cart(first)
        self._add_to_cart(second, quantity=2)
        response = self._checkout_post()
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 2)

    def test_checkout_stores_seller_snapshot(self):
        product = create_product(
            seller_name='AG Parts',
            whatsapp_number='+7 777 000 00 00',
        )
        self._checkout_post(product)
        order = Order.objects.get()
        self.assertEqual(order.seller_name, 'AG Parts')
        self.assertEqual(order.seller_whatsapp, '+7 777 000 00 00')

    def test_mixed_cart_cannot_checkout(self):
        first = create_product(article='ONE-1', whatsapp_number='+77771111111', seller_name='Seller One')
        second = create_product(
            article='TWO-1',
            whatsapp_number='+77772222222',
            seller_name='Seller Two',
        )
        session = self.client.session
        session[SESSION_CART_KEY] = {
            str(first.id): 1,
            str(second.id): 1,
        }
        session.save()
        checkout_url = reverse('orders:checkout')
        self.client.get(checkout_url)
        response = self.client.post(checkout_url, data={
            'customer_name': 'Иван',
            'customer_phone': '+7 (701) 123-45-67',
            'delivery_method': Order.DELIVERY_PICKUP,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 0)

    def test_checkout_does_not_create_kaspi_transaction(self):
        product = create_product()
        self._checkout_post(product)
        self.assertEqual(KaspiTransaction.objects.count(), 0)

    def test_checkout_does_not_mark_order_paid(self):
        product = create_product()
        self._checkout_post(product)
        order = Order.objects.get()
        self.assertNotEqual(order.status, Order.STATUS_PAID)

    def test_order_gets_access_token(self):
        product = create_product()
        self._checkout_post(product)
        order = Order.objects.get()
        self.assertIsNotNone(order.access_token)
        self.assertIsInstance(order.access_token, uuid.UUID)

    def test_order_success_with_valid_token(self):
        product = create_product()
        response = self._checkout_post(product)
        order = Order.objects.get()
        success_url = reverse(
            'orders:order_success',
            kwargs={'order_id': order.pk, 'access_token': order.access_token},
        )
        self.assertEqual(response['Location'], success_url)
        page = self.client.get(success_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'Заказ №{order.id} принят')

    def test_order_success_with_invalid_token_returns_404(self):
        product = create_product()
        self._checkout_post(product)
        order = Order.objects.get()
        bad_url = reverse(
            'orders:order_success',
            kwargs={'order_id': order.pk, 'access_token': uuid.uuid4()},
        )
        response = self.client.get(bad_url)
        self.assertEqual(response.status_code, 404)

    @patch('orders.views.send_order_admin_email')
    def test_checkout_schedules_admin_email(self, mock_send):
        product = create_product()
        with self.captureOnCommitCallbacks(execute=True):
            self._checkout_post(product)
        order = Order.objects.get()
        mock_send.assert_called_once_with(order.pk)

    def test_admin_email_sent_to_order_admin_email(self):
        product = create_product(price=9280, seller_name='AG Parts')
        self._checkout_post(product)
        order = Order.objects.get()
        send_order_admin_email(order.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('orders-admin@test.local', mail.outbox[0].to)
        self.assertIn('AG Parts', mail.outbox[0].subject)
        self.assertIn('9 280', mail.outbox[0].subject)

    def test_admin_email_contains_required_fields(self):
        first = create_product(title='Spark plugs', article='ABC-123', price=9280)
        second = create_product(
            title='Air filter',
            article='AF-500',
            whatsapp_number='87771234567',
            price=4000,
        )
        self._add_to_cart(first)
        self._add_to_cart(second, quantity=2)
        self._checkout_post()
        order = Order.objects.get()
        send_order_admin_email(order.pk)
        body = mail.outbox[0].body
        self.assertIn(f'№{order.id}', body)
        self.assertIn('AG Parts', body)
        self.assertIn('Иван', body)
        self.assertIn('+77011234567', body)
        self.assertIn('Spark plugs', body)
        self.assertIn('ABC-123', body)
        self.assertIn('Air filter', body)
        self.assertIn('AF-500', body)
        self.assertIn('https://wa.me/77011234567', body)
        self.assertIn('/admin/orders/order/', body)

    @patch('orders.email_notifications.send_mail', side_effect=Exception('SMTP failed'))
    def test_smtp_error_does_not_delete_order(self, mock_send_mail):
        product = create_product()
        self._checkout_post(product)
        order = Order.objects.get()
        result = send_order_admin_email(order.pk)
        self.assertFalse(result)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    @patch('orders.views.send_order_admin_email', return_value=False)
    def test_smtp_error_still_shows_success_page(self, mock_send):
        product = create_product()
        with self.captureOnCommitCallbacks(execute=True):
            response = self._checkout_post(product)
        order = Order.objects.get()
        success_url = reverse(
            'orders:order_success',
            kwargs={'order_id': order.pk, 'access_token': order.access_token},
        )
        self.assertEqual(response['Location'], success_url)
        page = self.client.get(success_url)
        self.assertEqual(page.status_code, 200)

    def test_checkout_page_has_submit_button_text(self):
        product = create_product()
        self._add_to_cart(product)
        response = self.client.get(reverse('orders:checkout'))
        self.assertContains(response, 'Оформить заказ')
        self.assertNotContains(response, 'Перейти к оплате Kaspi')

    def test_checkout_page_uses_total_label(self):
        product = create_product()
        self._add_to_cart(product)
        response = self.client.get(reverse('orders:checkout'))
        self.assertContains(response, 'Итого')
        self.assertNotContains(response, 'К оплате')

    def test_success_page_has_payment_warning(self):
        product = create_product()
        self._checkout_post(product)
        order = Order.objects.get()
        response = self.client.get(reverse(
            'orders:order_success',
            kwargs={'order_id': order.pk, 'access_token': order.access_token},
        ))
        self.assertContains(
            response,
            'Не оплачивайте товар до подтверждения заказа продавцом',
        )
        self.assertNotContains(response, 'Симулировать успешную оплату')
        self.assertContains(response, 'noindex,nofollow')

    def test_mock_payment_url_not_available(self):
        with self.assertRaises(NoReverseMatch):
            reverse('orders:order_payment')

    def test_checkout_clears_cart_via_manager(self):
        product = create_product()
        self._add_to_cart(product)
        self._checkout_post()
        count_response = self.client.get(reverse('orders:cart_count'))
        self.assertEqual(count_response.json()['cart_count'], 0)

    def test_update_quantity_rejects_other_seller_with_409(self):
        first = create_product(article='QTY-1', whatsapp_number='+77771111111')
        second = create_product(
            article='QTY-2',
            whatsapp_number='+77772222222',
            seller_name='Other Seller',
        )
        self._add_to_cart(first)
        response = self.client.post(
            reverse('orders:cart_update_quantity'),
            data=json.dumps({
                'product_id': second.id,
                'quantity': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 409)

    def test_authenticated_user_cart_enforces_single_seller(self):
        user = User.objects.create_user(username='buyer1', password='secret12345')
        self.client.login(username='buyer1', password='secret12345')
        first = create_product(article='AUTH-1', whatsapp_number='+77771111111')
        second = create_product(
            article='AUTH-2',
            whatsapp_number='+77772222222',
            seller_name='Other Seller',
        )
        self._add_to_cart(first)
        response = self._add_to_cart(second)
        self.assertEqual(response.status_code, 409)

    def test_legacy_success_url_without_token_is_unavailable(self):
        product = create_product()
        self._checkout_post(product)
        order = Order.objects.get()
        response = self.client.get(f'/orders/{order.pk}/success/')
        self.assertEqual(response.status_code, 404)

    def test_email_delivery_pickup_contains_warehouse_address(self):
        product = create_product()
        self._checkout_post(product)
        order = Order.objects.get()
        delivery_text = format_delivery_block(order)
        self.assertIn('Самовывоз', delivery_text)

    def test_email_delivery_courier_contains_address_parts(self):
        product = create_product()
        self._add_to_cart(product)
        checkout_url = reverse('orders:checkout')
        self.client.get(checkout_url)
        self.client.post(checkout_url, data={
            'customer_name': 'Иван',
            'customer_phone': '+7 (701) 123-45-67',
            'delivery_method': Order.DELIVERY_COURIER,
            'courier_street': 'Абая',
            'courier_house': '10',
            'courier_apartment': '25',
        })
        order = Order.objects.get()
        delivery_text = format_delivery_block(order)
        self.assertIn('Абая', delivery_text)
        self.assertIn('д. 10', delivery_text)
        self.assertIn('кв. 25', delivery_text)

    def test_email_delivery_kz_contains_city_and_transport(self):
        product = create_product()
        self._add_to_cart(product)
        checkout_url = reverse('orders:checkout')
        self.client.get(checkout_url)
        self.client.post(checkout_url, data={
            'customer_name': 'Иван',
            'customer_phone': '+7 (701) 123-45-67',
            'delivery_method': Order.DELIVERY_KZ,
            'kz_city': 'Астана',
            'transport_company': 'cdek',
        })
        order = Order.objects.get()
        delivery_text = format_delivery_block(order)
        self.assertIn('Астана', delivery_text)
        self.assertIn('CDEK', delivery_text)

    @override_settings(ORDER_ADMIN_EMAIL='', EMAIL_HOST_USER='')
    def test_missing_admin_email_logs_warning_without_exception(self):
        product = create_product()
        self._checkout_post(product)
        order = Order.objects.get()
        with self.assertLogs('orders.email_notifications', level='WARNING') as logs:
            result = send_order_admin_email(order.pk)
        self.assertFalse(result)
        self.assertTrue(any('skip admin email' in entry for entry in logs.output))

    def test_email_uses_public_base_url_for_admin_link(self):
        product = create_product(article='')
        self._checkout_post(product)
        order = Order.objects.get()
        body = build_order_email_body(order)
        self.assertIn('https://zpt.kz/admin/orders/order/', body)
        self.assertIn('Артикул: —', body)
