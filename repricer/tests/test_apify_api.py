from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from repricer import apify_client as api
from repricer.apify_output import import_apify_result
from repricer.models import KaspiApifyRun, KaspiActiveCityCheck, KaspiActiveCompetitorOffer
from . import test_apify_output as fixtures


@override_settings(APIFY_API_TOKEN='test-secret-not-a-real-key')
class ApifyApiTests(TestCase):
    setUp = fixtures.ApifyOutputTests.setUp

    def item(self, price=2200, city='Almaty', seller='competitor'):
        return {'id': '141407587', 'url': self.listing.public_url,
            'title': self.product.title, 'city': city,
            'offers': [{'sellerId': seller, 'sellerName': 'Other', 'price': price}]}

    def info(self, **changes):
        return {'id': 'Run12345678901234', 'actId': api.ACTOR_ID,
            'status': 'SUCCEEDED', 'defaultDatasetId': 'Dataset1234567890',
            'defaultKeyValueStoreId': 'Store12345678901',
            'startedAt': timezone.now().isoformat(), 'usageTotalUsd': 0.1, **changes}

    def local(self, **changes):
        return KaspiApifyRun.objects.create(batch=self.batch, created_by=self.staff,
            external_id='Run12345678901234', status='SUCCEEDED',
            input_snapshot={'cities': ['Almaty']}, **changes)

    def test_new_price_can_rise_and_sellers_can_disappear(self):
        now = timezone.now()
        import_apify_result([self.item(2200), self.item(2100, seller='vanished')], self.batch,
                           filename='old', captured_at=now-timedelta(hours=1))
        import_apify_result([self.item(2600)], self.batch, filename='new', captured_at=now)
        self.active.refresh_from_db()
        self.assertEqual(self.active.competitor_min_price, 2600)
        self.assertEqual(list(KaspiActiveCompetitorOffer.objects.values_list('seller_id', flat=True)), ['competitor'])
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.last_known_our_price, 2700)

    def test_partial_city_preserves_other_city_and_old_import_cannot_resurrect_sellers(self):
        now = timezone.now()
        import_apify_result([self.item(2400, city='Astana')], self.batch, filename='a', captured_at=now)
        empty = self.item(); empty['offers'] = []
        import_apify_result([empty], self.batch, filename='empty', captured_at=now, cities=['Almaty'])
        import_apify_result([self.item(1000, seller='obsolete')], self.batch,
                           filename='older', captured_at=now-timedelta(hours=1), cities=['Almaty'])
        self.active.refresh_from_db()
        self.assertEqual(self.active.competitor_min_price, 2400)
        self.assertEqual(KaspiActiveCityCheck.objects.count(), 2)
        self.assertFalse(KaspiActiveCompetitorOffer.objects.filter(seller_id='obsolete').exists())

    def test_empty_new_check_removes_previous_price(self):
        import_apify_result([self.item()], self.batch, filename='one')
        empty = self.item(); empty['offers'] = []
        import_apify_result([empty], self.batch, filename='two')
        self.active.refresh_from_db()
        self.assertIsNone(self.active.competitor_min_price)
        self.assertEqual(self.active.competitor_scan_status, 'NO_OFFERS')

    def test_malformed_sellers_do_not_erase_good_prices(self):
        import_apify_result([self.item()], self.batch, filename='one')
        broken = self.item(); broken['offers'] = [{'price': 10}]
        import_apify_result([broken], self.batch, filename='bad')
        self.active.refresh_from_db()
        self.assertEqual(self.active.competitor_min_price, 2200)

    def test_unmatched_run_keeps_raw_results_and_reason_for_review(self):
        run = self.local()
        bad = self.item(); bad.update(id='999999999', url='https://kaspi.kz/shop/p/other-999999999/', title='Другая деталь')
        with patch.object(api, 'run_info', return_value=self.info()), patch.object(api, 'dataset_page', return_value=[bad]):
            response = self.client.post(reverse('repricer:apify_step', args=[run.pk]))
        self.assertEqual(response.status_code, 400)
        run.refresh_from_db()
        self.assertEqual(run.status, 'REVIEW')
        self.assertEqual(run.raw_items, [bad])
        self.assertIsNone(run.imported_at)

    def test_unknown_start_can_be_reconciled_only_with_matching_input(self):
        run = KaspiApifyRun.objects.create(batch=self.batch, created_by=self.staff,
            status='UNKNOWN', input_snapshot={'cities': ['Almaty'], 'searchQueries': ['exact']})
        with patch.object(api, 'run_info', return_value=self.info()), patch.object(api, 'run_input', return_value={'cities': ['Almaty'], 'searchQueries': ['wrong']}):
            self.client.post(reverse('repricer:apify_attach'), {'run_id': 'Run12345678901234', 'local_run_id': run.pk})
        run.refresh_from_db(); self.assertEqual(run.status, 'UNKNOWN')
        with patch.object(api, 'run_info', return_value=self.info()), patch.object(api, 'run_input', return_value=run.input_snapshot):
            self.client.post(reverse('repricer:apify_attach'), {'run_id': 'Run12345678901234', 'local_run_id': run.pk})
        run.refresh_from_db(); self.assertEqual(run.status, 'SUCCEEDED')

    def test_attach_is_idempotent_and_does_not_start_actor(self):
        with patch.object(api, 'run_info', return_value=self.info()), patch.object(api, 'run_input', return_value={'cities': ['Almaty']}), patch.object(api, 'start_run') as start:
            for _ in range(2):
                response = self.client.post(reverse('repricer:apify_attach'), {'run_id': 'Run12345678901234'})
                self.assertEqual(response.status_code, 302)
            self.assertEqual(KaspiApifyRun.objects.count(), 1)
            start.assert_not_called()

    def test_page_fetch_resumes_and_imports_once_with_source_timestamp(self):
        run = self.local()
        captured = timezone.now()-timedelta(days=1)
        info = self.info(startedAt=captured.isoformat())
        with patch.object(api, 'run_info', return_value=info), patch.object(api, 'dataset_page', side_effect=[[self.item()]*500, [self.item(2000)]]) as pages:
            url = reverse('repricer:apify_step', args=[run.pk])
            self.assertFalse(self.client.post(url).json()['done'])
            self.active.refresh_from_db()
            self.assertIsNone(self.active.competitor_min_price)
            self.assertTrue(self.client.post(url).json()['done'])
            self.assertTrue(self.client.post(url).json()['done'])
            self.assertEqual(pages.call_count, 2)
            self.assertEqual(pages.call_args.args[1], 500)
        self.active.refresh_from_db()
        self.assertEqual(self.active.competitor_checked_at, captured)
        self.assertEqual(self.active.competitor_min_price, 2000)

    def test_import_is_bound_to_original_active(self):
        run = self.local()
        with patch('repricer.apify_api._latest_active_batch', return_value=None), patch.object(api, 'run_info', return_value=self.info()), patch.object(api, 'dataset_page', return_value=[self.item()]):
            self.assertEqual(self.client.post(reverse('repricer:apify_step', args=[run.pk])).status_code, 200)
        self.active.refresh_from_db()
        self.assertEqual(self.active.competitor_min_price, 2200)

    def test_start_requires_budget_and_charge_confirmation(self):
        with patch.object(api, 'start_run') as start:
            for data in ({}, {'max_charge_usd': '1'}, {'authorize_charge': 'yes', 'max_charge_usd': 'NaN'}, {'authorize_charge': 'yes', 'max_charge_usd': '6'}):
                self.client.post(reverse('repricer:apify_start'), data)
            start.assert_not_called()
            self.assertFalse(KaspiApifyRun.objects.exists())

    def test_timeout_never_automatically_retries_paid_start(self):
        with patch.object(api, 'start_run', side_effect=api.ApifyError('Нет ответа')) as start:
            data = {'authorize_charge': 'yes', 'max_charge_usd': '1', 'scope': 'remaining'}
            self.client.post(reverse('repricer:apify_start'), data)
            self.client.post(reverse('repricer:apify_start'), data)
            self.assertEqual(start.call_count, 1)
            self.assertEqual(KaspiApifyRun.objects.get().status, 'UNKNOWN')

    def test_only_missing_rows_are_sent_and_duplicate_start_is_blocked(self):
        import_apify_result([self.item()], self.batch, filename='first')
        with patch.object(api, 'start_run', return_value=self.info(status='RUNNING')) as start:
            data = {'authorize_charge': 'yes', 'max_charge_usd': '0.50', 'scope': 'remaining'}
            self.client.post(reverse('repricer:apify_start'), data)
            self.client.post(reverse('repricer:apify_start'), data)
            self.assertEqual(start.call_count, 1)
            payload = start.call_args.args[0]
            self.assertFalse(any('1017110XEN01' in query for query in payload['searchQueries']))

    def test_pending_run_fetch_does_not_fetch_dataset(self):
        run = self.local()
        with patch.object(api, 'run_info', return_value=self.info(status='RUNNING')), patch.object(api, 'dataset_page') as page:
            self.assertFalse(self.client.post(reverse('repricer:apify_step', args=[run.pk])).json()['done'])
            page.assert_not_called()

    def test_authorization_and_post_required(self):
        self.assertEqual(self.client.get(reverse('repricer:apify_attach')).status_code, 405)
        self.client.logout()
        with patch.object(api, 'run_info') as read:
            self.assertEqual(self.client.post(reverse('repricer:apify_attach'), {'run_id': 'Run12345678901234'}).status_code, 302)
            read.assert_not_called()

    def test_console_and_dashboard_render_honest_coverage(self):
        self.assertContains(self.client.get(reverse('repricer:apify_api')), 'Прогоны Apify')
        self.assertContains(self.client.get(reverse('repricer:dashboard')), 'Фактическое покрытие городов')


class ApifyClientTests(SimpleTestCase):
    @override_settings(APIFY_API_TOKEN='private-token')
    @patch('repricer.apify_client.requests.request')
    def test_cap_and_authorization_header_without_secret_in_url(self, send):
        response = MagicMock(status_code=201)
        response.iter_content.return_value = [b'{"data":{"id":"run"}}']
        send.return_value.__enter__.return_value = response
        api.start_run({'cities': ['Almaty']}, api.budget('0.50'))
        args, kwargs = send.call_args
        self.assertNotIn('private-token', args[1])
        self.assertEqual(kwargs['params']['maxTotalChargeUsd'], '0.50')
        self.assertEqual(kwargs['params']['restartOnError'], 'false')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer private-token')

    def test_bad_identifiers_cannot_change_provider_endpoint(self):
        for value in ('../secrets', 'http://other.test', 'abc?token=secret'):
            with self.assertRaises(api.ApifyError):
                api.identifier(value)
