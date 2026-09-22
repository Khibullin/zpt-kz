from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.models import Order
from payments.exceptions import PaymentStartBlocked
from payments.models import PaymentAttempt
from payments.services import start_test_payment
from payments.signatures import sign_init

from .helpers import (
    PASS1,
    enabled_settings,
    make_order,
    make_product,
    make_seller,
    make_users,
)


class StartPaymentTests(TestCase):
    def setUp(self):
        self.superuser, self.staff, self.plain = make_users()
        self.seller = make_seller('own-seller')
        self.product = make_product(self.seller, 'OWN-1', price=1000)
        self.order = make_order([(self.product, 1)])
        self.start_url = reverse(
            'admin:orders_order_robokassa_test',
            args=[self.order.pk],
        )

    def _settings(self, **extra):
        data = enabled_settings(self.seller)
        data.update(extra)
        return override_settings(**data)

    def test_superuser_start_posts_to_kz_endpoint_with_istest(self):
        with self._settings():
            self.client.force_login(self.superuser)
            response = self.client.post(self.start_url, follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'https://auth.robokassa.kz/Merchant/Index.aspx')
        self.assertContains(response, 'name="IsTest" value="1"')
        self.assertNotContains(response, 'Receipt')
        attempt = PaymentAttempt.objects.get()
        self.assertEqual(attempt.mode, PaymentAttempt.MODE_TEST)
        self.assertEqual(attempt.amount, Decimal('1000.00'))
        self.assertEqual(attempt.currency, 'KZT')
        self.assertEqual(attempt.hash_algo, 'sha256')
        self.assertEqual(attempt.merchant_login, 'zptkz')
        self.assertEqual(attempt.seller_profile_id, self.seller.pk)
        expected = sign_init('zptkz', '1000.00', str(attempt.inv_id), PASS1)
        self.assertContains(response, expected)
        self.assertEqual(self.order.status, Order.STATUS_NEW)

    def test_start_is_idempotent_without_force_new(self):
        with self._settings():
            first = start_test_payment(self.superuser, self.order.pk)
            second = start_test_payment(self.superuser, self.order.pk)
        self.assertTrue(second['reused'])
        self.assertEqual(first['attempt'].inv_id, second['attempt'].inv_id)
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def test_force_new_creates_new_invid(self):
        with self._settings():
            first = start_test_payment(self.superuser, self.order.pk)
            second = start_test_payment(
                self.superuser,
                self.order.pk,
                force_new=True,
            )
        self.assertFalse(second['reused'])
        self.assertNotEqual(first['attempt'].inv_id, second['attempt'].inv_id)
        self.assertEqual(PaymentAttempt.objects.count(), 2)

    def test_anonymous_staff_and_user_cannot_start(self):
        with self._settings():
            self.assertEqual(self.client.get(self.start_url).status_code, 302)
            self.client.force_login(self.plain)
            self.assertEqual(self.client.get(self.start_url).status_code, 302)
            self.client.force_login(self.staff)
            response = self.client.post(self.start_url, {'force_new': '1'})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_disabled_flags_and_empty_secrets_block_start(self):
        self.client.force_login(self.superuser)
        blocked = dict(enabled_settings(self.seller))
        blocked['ROBOKASSA_ENABLED'] = False
        with override_settings(**blocked):
            response = self.client.post(self.start_url)
            self.assertContains(response, 'выключена', status_code=200)
        blocked = dict(enabled_settings(self.seller))
        blocked['ROBOKASSA_TEST_ENABLED'] = False
        with override_settings(**blocked):
            response = self.client.post(self.start_url)
            self.assertContains(response, 'выключена', status_code=200)
        blocked = dict(enabled_settings(self.seller))
        blocked['ROBOKASSA_PASS1_TEST'] = ''
        with override_settings(**blocked):
            response = self.client.post(self.start_url)
            self.assertContains(response, 'парол', status_code=200)
        blocked = dict(enabled_settings(self.seller))
        blocked['ROBOKASSA_OWN_SELLER_PROFILE_ID'] = ''
        with override_settings(**blocked):
            response = self.client.post(self.start_url)
            self.assertContains(response, 'ROBOKASSA_OWN_SELLER_PROFILE_ID', status_code=200)
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_foreign_unbound_and_mixed_items_blocked(self):
        other = make_seller('other-seller', name='Other', phone='77002220002')
        foreign = make_product(other, 'FOR-1', seller_name=self.seller.name)
        unbound = make_product(
            None,
            'LEG-1',
            seller_name=self.seller.name,
            whatsapp_number=self.seller.phone,
        )
        mixed = make_order([(self.product, 1), (foreign, 1)])
        unbound_order = make_order([(unbound, 1)])
        paid = make_order([(self.product, 1)], status=Order.STATUS_PAID)
        cancelled = make_order([(self.product, 1)], status=Order.STATUS_CANCELLED)
        with self._settings():
            with self.assertRaises(PaymentStartBlocked):
                start_test_payment(self.superuser, mixed.pk)
            with self.assertRaises(PaymentStartBlocked):
                start_test_payment(self.superuser, unbound_order.pk)
            with self.assertRaises(PaymentStartBlocked):
                start_test_payment(self.superuser, paid.pk)
            with self.assertRaises(PaymentStartBlocked):
                start_test_payment(self.superuser, cancelled.pk)
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_live_flag_cannot_create_live_payment(self):
        with self._settings(ROBOKASSA_LIVE_ENABLED=True):
            payload = start_test_payment(self.superuser, self.order.pk)
        self.assertEqual(payload['fields']['IsTest'], '1')
        self.assertEqual(payload['attempt'].mode, 'test')
        self.assertEqual(
            payload['action'],
            'https://auth.robokassa.kz/Merchant/Index.aspx',
        )

    def test_start_does_not_call_checkout_or_email(self):
        with self._settings():
            with patch('orders.views.checkout') as checkout:
                with patch('orders.email_notifications.send_order_admin_email') as email:
                    start_test_payment(self.superuser, self.order.pk)
        checkout.assert_not_called()
        email.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    def test_page_explains_goods_total_without_delivery(self):
        self.client.force_login(self.superuser)
        with self._settings():
            response = self.client.get(self.start_url)
        self.assertContains(response, 'сумма товаров')
        self.assertContains(response, 'Стоимость доставки не добавляется')
        self.assertContains(response, 'Receipt')

    def test_start_post_requires_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.superuser)
        with self._settings():
            response = client.post(self.start_url, {'force_new': '1'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(PaymentAttempt.objects.count(), 0)
