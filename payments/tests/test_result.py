import hashlib
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from catalog.models import StockMovement, Warehouse
from catalog.stock_service import apply_stock_movement, get_stock_quantity
from catalog.warehouses import WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2
from orders.models import Order
from payments.models import PaymentAttempt
from payments.services import start_test_payment
from payments.signatures import sign_result

from .helpers import (
    PASS2,
    enabled_settings,
    make_order,
    make_product,
    make_seller,
    make_users,
)


class ResultTests(TestCase):
    def setUp(self):
        self.superuser, self.staff, self.plain = make_users()
        self.seller = make_seller('own-seller-result')
        self.product = make_product(self.seller, 'OWN-R1', price=1000, stock_qty=9)
        self.order = make_order([(self.product, 1)])
        self.url = reverse('payments:robokassa_result')
        Warehouse.objects.get_or_create(
            code=WAREHOUSE_CODE_PP1,
            defaults={'name': 'Основной склад'},
        )
        Warehouse.objects.get_or_create(
            code=WAREHOUSE_CODE_PP2,
            defaults={'name': 'Fulfillment'},
        )
        apply_stock_movement(
            product=self.product,
            warehouse=WAREHOUSE_CODE_PP1,
            movement_type=StockMovement.MovementType.OPENING,
            quantity_delta=5,
            source='test',
        )
        apply_stock_movement(
            product=self.product,
            warehouse=WAREHOUSE_CODE_PP2,
            movement_type=StockMovement.MovementType.OPENING,
            quantity_delta=3,
            source='test',
        )

    def _settings(self, **extra):
        data = enabled_settings(self.seller)
        data.update(extra)
        return override_settings(**data)

    def _start(self):
        with self._settings():
            return start_test_payment(self.superuser, self.order.pk)['attempt']

    def _payload(self, attempt, *, out_sum='1000.00', password=PASS2, **extra):
        inv_id = extra.pop('inv_id', str(attempt.inv_id))
        shp = {
            key: value
            for key, value in extra.items()
            if key.startswith('Shp_')
        }
        signature = extra.pop(
            'SignatureValue',
            sign_result(out_sum, inv_id, password, shp or None),
        )
        data = {
            'OutSum': out_sum,
            'InvId': inv_id,
            'SignatureValue': signature,
        }
        data.update(extra)
        return data

    def test_valid_result_confirms_attempt_not_order(self):
        attempt = self._start()
        pp1 = get_stock_quantity(self.product, WAREHOUSE_CODE_PP1)
        pp2 = get_stock_quantity(self.product, WAREHOUSE_CODE_PP2)
        with self._settings():
            with patch('django.core.mail.send_mail') as send_mail:
                with patch('orders.email_notifications.send_order_admin_email') as order_email:
                    response = self.client.post(self.url, self._payload(attempt))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), f'OK{attempt.inv_id}')
        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_CONFIRMED)
        self.assertIsNotNone(attempt.confirmed_at)
        self.assertEqual(self.order.status, Order.STATUS_NEW)
        self.assertEqual(self.product.stock_qty, 9)
        self.assertEqual(get_stock_quantity(self.product, WAREHOUSE_CODE_PP1), pp1)
        self.assertEqual(get_stock_quantity(self.product, WAREHOUSE_CODE_PP2), pp2)
        self.assertFalse(
            StockMovement.objects.filter(
                movement_type=StockMovement.MovementType.SALE,
            ).exists()
        )
        send_mail.assert_not_called()
        order_email.assert_not_called()

    def test_result_works_when_start_flags_are_off(self):
        attempt = self._start()
        with override_settings(
            **{**enabled_settings(self.seller), 'ROBOKASSA_ENABLED': False, 'ROBOKASSA_TEST_ENABLED': False}
        ):
            response = self.client.post(self.url, self._payload(attempt))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), f'OK{attempt.inv_id}')

    def test_replay_returns_same_ok(self):
        attempt = self._start()
        with self._settings():
            first = self.client.post(self.url, self._payload(attempt))
            second = self.client.post(self.url, self._payload(attempt))
        self.assertEqual(first.content, second.content)
        self.assertEqual(PaymentAttempt.objects.filter(status='confirmed').count(), 1)

    def test_bad_signature_sum_nan_unknown_and_duplicates(self):
        attempt = self._start()
        with self._settings():
            bad_sign = self.client.post(
                self.url,
                self._payload(attempt, SignatureValue='0' * 64),
            )
            bad_sum = self.client.post(
                self.url,
                self._payload(attempt, out_sum='1000.01'),
            )
            nan = self.client.post(
                self.url,
                {
                    'OutSum': 'NaN',
                    'InvId': str(attempt.inv_id),
                    'SignatureValue': sign_result(
                        'NaN',
                        str(attempt.inv_id),
                        PASS2,
                    ),
                },
            )
            infinity = self.client.post(
                self.url,
                {
                    'OutSum': 'Infinity',
                    'InvId': str(attempt.inv_id),
                    'SignatureValue': 'abc',
                },
            )
            unknown = self.client.post(
                self.url,
                {
                    'OutSum': '1000.00',
                    'InvId': '2147483646',
                    'SignatureValue': sign_result('1000.00', '2147483646', PASS2),
                },
            )
            duplicate = self.client.post(
                self.url + '?OutSum=1000.00',
                self._payload(attempt),
            )
        for response in (bad_sign, bad_sum, nan, infinity, unknown, duplicate):
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(b'OK', response.content)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_CREATED)
        self.assertEqual(self.order.status, Order.STATUS_NEW)

    def test_raw_outsum_with_extra_zeros_matches_decimal_snapshot(self):
        attempt = self._start()
        raw = '1000.000000'
        payload = self._payload(attempt, out_sum=raw)
        self.assertEqual(
            payload['SignatureValue'],
            hashlib.sha256(f'{raw}:{attempt.inv_id}:{PASS2}'.encode()).hexdigest(),
        )
        with self._settings():
            response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        attempt.refresh_from_db()
        self.assertEqual(attempt.amount, Decimal('1000.00'))

    def test_live_password_is_not_accepted(self):
        attempt = self._start()
        with self._settings():
            response = self.client.post(
                self.url,
                self._payload(attempt, password='live-password-must-not-work'),
            )
        self.assertEqual(response.status_code, 400)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_CREATED)

    def test_get_result_is_accepted(self):
        attempt = self._start()
        payload = self._payload(attempt)
        with self._settings():
            response = self.client.get(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), f'OK{attempt.inv_id}')


class ConcurrentResultTests(TransactionTestCase):
    def setUp(self):
        self.superuser, _staff, _plain = make_users()
        self.seller = make_seller('own-seller-conc')
        self.product = make_product(self.seller, 'OWN-C1')
        self.order = make_order([(self.product, 1)])

    def test_parallel_result_is_idempotent_on_postgres(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'select_for_update concurrency is not proven on '
                f'{connection.vendor}; use PostgreSQL to verify locking.'
            )
        from concurrent.futures import ThreadPoolExecutor

        with override_settings(**enabled_settings(self.seller)):
            attempt = start_test_payment(self.superuser, self.order.pk)['attempt']
            url = reverse('payments:robokassa_result')
            payload = {
                'OutSum': '1000.00',
                'InvId': str(attempt.inv_id),
                'SignatureValue': sign_result(
                    '1000.00',
                    str(attempt.inv_id),
                    PASS2,
                ),
            }

            def post():
                from django.test import Client
                return Client().post(url, payload)

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: post(), range(2)))
        self.assertEqual({item.status_code for item in results}, {200})
        self.assertEqual(
            {item.content.decode() for item in results},
            {f'OK{attempt.inv_id}'},
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_CONFIRMED)
