from django.test import TestCase, override_settings
from django.urls import reverse

from payments.models import PaymentAttempt
from payments.services import start_test_payment
from payments.signatures import sign_success

from .helpers import PASS1, enabled_settings, make_order, make_product, make_seller, make_users


class ReturnUrlTests(TestCase):
    def setUp(self):
        self.superuser, self.staff, self.plain = make_users()
        self.seller = make_seller('own-seller-return')
        self.product = make_product(self.seller, 'OWN-S1')
        self.order = make_order([(self.product, 1)])
        self.success_url = reverse('payments:robokassa_success')
        self.fail_url = reverse('payments:robokassa_fail')

    def _settings(self):
        return override_settings(**enabled_settings(self.seller))

    def _start(self):
        with self._settings():
            return start_test_payment(self.superuser, self.order.pk)['attempt']

    def _success_payload(self, attempt):
        out_sum = '1000.00'
        inv_id = str(attempt.inv_id)
        return {
            'OutSum': out_sum,
            'InvId': inv_id,
            'SignatureValue': sign_success(out_sum, inv_id, PASS1),
        }

    def test_anonymous_success_hides_order_and_token(self):
        attempt = self._start()
        response = self.client.get(self.success_url, self._success_payload(attempt))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'noindex')
        self.assertNotContains(response, self.order.customer_name)
        self.assertNotContains(response, self.order.customer_phone)
        self.assertNotContains(response, str(self.order.access_token))
        self.assertNotContains(response, f'InvId: {attempt.inv_id}')
        self.assertNotContains(response, f'№{self.order.pk}')

    def test_staff_without_superuser_is_treated_as_public(self):
        attempt = self._start()
        self.client.force_login(self.staff)
        response = self.client.get(self.success_url, self._success_payload(attempt))
        self.assertNotContains(response, f'InvId: {attempt.inv_id}')
        self.assertNotContains(response, self.order.customer_phone)

    def test_superuser_success_before_result_shows_waiting(self):
        attempt = self._start()
        self.client.force_login(self.superuser)
        with self._settings():
            response = self.client.get(self.success_url, self._success_payload(attempt))
        self.assertContains(response, 'до серверного уведомления')
        self.assertContains(response, f'InvId: {attempt.inv_id}')
        self.assertContains(response, 'не подтверждает оплату')
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_CREATED)

    def test_fail_after_result_does_not_unconfirm(self):
        attempt = self._start()
        result_url = reverse('payments:robokassa_result')
        from payments.signatures import sign_result
        from .helpers import PASS2
        with self._settings():
            self.client.post(result_url, {
                'OutSum': '1000.00',
                'InvId': str(attempt.inv_id),
                'SignatureValue': sign_result(
                    '1000.00',
                    str(attempt.inv_id),
                    PASS2,
                ),
            })
            self.client.force_login(self.superuser)
            response = self.client.get(self.fail_url, {'InvId': str(attempt.inv_id)})
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_CONFIRMED)
        self.assertContains(response, 'не отменяет')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'new')

    def test_shp_access_token_is_not_sent_on_start(self):
        with self._settings():
            payload = start_test_payment(self.superuser, self.order.pk)
        joined = '&'.join(
            f'{key}={value}' for key, value in payload['fields'].items()
        )
        self.assertNotIn('Shp_', joined)
        self.assertNotIn(str(self.order.access_token), joined)
        self.assertEqual(payload['fields']['IsTest'], '1')
