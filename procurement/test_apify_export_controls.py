import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Activity, Membership, Offer, PriceUpdate, Selection, Supplier, WholesaleSelectionRule
from .omega_selection_requests import REQUEST_ACTION


class ApifyExportControlsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('apify-controls')
        Membership.objects.create(user=cls.user, role='admin')
        cls.omega = Supplier.objects.create(name='Omega')
        cls.phaeton = Supplier.objects.create(name='Phaeton DC')
        WholesaleSelectionRule.objects.create(singleton_key=1, supplier=cls.phaeton)
        cls.price = PriceUpdate.objects.create(supplier=cls.phaeton, user=cls.user,
            filename='source.xlsx', checked_on=date.today(), sha256='source', status='done', row_count=1,
            rows=[dict(brand='TEST', article='A', name='Рычаг', cost='6000', stock='3', category='Рычаг')])
        cls.saved = Activity.objects.create(user=cls.user, action=REQUEST_ACTION, details={
            'supplier_id': cls.phaeton.pk, 'supplier_name': cls.phaeton.name,
            'number': 1, 'title': 'Запрос №1', 'query_count': 9915,
            'total': 7770, 'min_cost': '2000', 'price_batch_id': cls.price.pk,
            'price_sha256': cls.price.sha256, 'category_keys': ['рычаг']})
        cls.offer = Offer.objects.create(supplier=cls.phaeton, brand='TEST', article='A',
                                        name='Рычаг', cost=Decimal('6000'), stock='3')
        Selection.objects.create(user=cls.user, offer=cls.offer, quantity=2)

    def setUp(self):
        self.client.force_login(self.user)

    def test_all_sizes_cover_exactly_the_same_queries_without_overlap(self):
        queries = [dict(query=f'TEST A{i}', brand='TEST', article=f'A{i}') for i in range(9915)]
        url = reverse('procurement:selection_apify_json')
        with patch('procurement.omega_selection_requests.request_queries', return_value=queries):
            for size, count in [(500,20),(1000,10),(1500,7),(2000,5)]:
                collected = []
                for batch in range(1, count+1):
                    response = self.client.get(url, {'request_id': self.saved.pk, 'batch': batch,
                        'batch_size': size, 'city': ['Almaty'], 'cards': 3})
                    self.assertEqual(response.status_code, 200)
                    data = json.loads(response.content)
                    self.assertLessEqual(len(data['searchQueries']), size)
                    self.assertEqual(data['cities'], ['Almaty'])
                    self.assertEqual(data['maxItemsPerQuery'], 3)
                    collected.extend(data['searchQueries'])
                    if size != 500:
                        self.assertIn(f'_size{size}.json', response['Content-Disposition'])
                self.assertEqual(collected, [item['query'] for item in queries])
                self.assertEqual(self.client.get(url, {'request_id': self.saved.pk,
                    'batch_size': size, 'batch': count+1}).status_code, 404)

    def test_unsupported_sizes_rejected_before_query_generation(self):
        with patch('procurement.omega_selection_requests.request_queries') as generate:
            for value in ('', '0', '499', '5000', '-500', '1500.5', 'NaN'):
                self.assertEqual(self.client.get(reverse('procurement:selection_apify_json'),
                    {'request_id': self.saved.pk, 'batch_size': value}).status_code, 400)
            generate.assert_not_called()

    def test_default_remains_500_and_old_download_links_work(self):
        with patch('procurement.omega_selection_requests.request_queries', return_value=[
                dict(query=f'TEST {i}') for i in range(501)]):
            response = self.client.get(reverse('procurement:selection_apify_json'), {'request_id': self.saved.pk})
        self.assertEqual(len(json.loads(response.content)['searchQueries']), 500)
        self.assertIn(f'apify_input_{self.saved.pk}_01.json', response['Content-Disposition'])

    def test_page_recomputes_main_and_historical_batches(self):
        for size, count in [(500,20),(1000,10),(1500,7),(2000,5)]:
            page = self.client.get(reverse('procurement:selection_rules'), {
                'supplier': self.phaeton.pk, 'saved_request': self.saved.pk, 'batch_size': size})
            self.assertEqual(page.status_code, 200)
            self.assertEqual(len(page.context['just_saved_batches']), count)
            self.assertEqual(len(page.context['omega_requests'][0]['export_batches']), count)
            self.assertContains(page, 'Сбросить подготовку')
            self.assertContains(page, f'value="{size}" selected')

    def test_reset_only_clears_current_preparation_and_preserves_data(self):
        session = self.client.session
        session['omega_confirmed_preview'] = 'fingerprint'
        session['apify_buying_session_id'] = 1234
        session.save()
        before = self.saved.details.copy()
        response = self.client.post(reverse('procurement:selection_rules') +
            f'?saved_request={self.saved.pk}&batch_size=2000', {
                'action':'reset_apify_preparation', 'supplier':self.phaeton.pk}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['just_saved_request'])
        self.assertEqual(response.context['selected_batch_size'], 500)
        self.assertNotIn('saved_request=', response.redirect_chain[0][0])
        self.assertNotIn('omega_confirmed_preview', self.client.session)
        self.assertEqual(self.client.session['apify_buying_session_id'], 1234)
        self.saved.refresh_from_db(); self.price.refresh_from_db(); self.offer.refresh_from_db()
        self.assertEqual(self.saved.details, before)
        self.assertEqual(self.price.row_count, 1)
        self.assertEqual(self.offer.cost, Decimal('6000'))
        self.assertEqual(Selection.objects.get(user=self.user).quantity, 2)
        self.assertEqual(response.context['omega_requests'][0]['id'], self.saved.pk)

    def test_generation_button_is_available_without_active_request(self):
        page = self.client.get(reverse('procurement:selection_rules'), {'supplier': self.phaeton.pk})
        self.assertContains(page, 'id="apify-generate"')
        self.assertContains(page, 'Сформировать JSON для Apify')
        self.assertContains(page, 'id="apify-export-request"')
        self.assertContains(page, f'value="{self.saved.pk}" data-min-cost="2000"')
        self.assertIsNone(page.context['just_saved_request'])

    def test_saving_confirmed_draft_preserves_export_settings(self):
        url = reverse('procurement:selection_rules')
        data = {'supplier': self.phaeton.pk, 'price_batch_id': self.price.pk,
                'omega_category':['Рычаг'], 'omega_min_cost':'2000',
                'omega_max_vehicle_age':'20', 'omega_stock_submitted':'1',
                'omega_in_stock_only':'on', 'action':'preview_omega_request'}
        self.assertEqual(self.client.post(url, data).status_code, 200)
        data.update(action='save_omega_request', apify_export_settings='1',
                    apify_generate='1', apify_batch_size='2000', apify_cards='1', apify_city=['Almaty'])
        page = self.client.post(url, data, follow=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('#apify-step-title', page.redirect_chain[0][0])
        self.assertEqual(page.context['selected_batch_size'], 2000)
        self.assertEqual(page.context['export_cards'], 1)
        self.assertEqual(page.context['export_cities'], ['Almaty'])
        self.assertIsNotNone(page.context['just_saved_request'])
        self.assertEqual(page.context['just_saved_request'].details['number'], 2)

    def test_active_request_cannot_borrow_another_supplier_snapshot(self):
        page = self.client.get(reverse('procurement:selection_rules'), {
            'supplier':self.omega.pk, 'saved_request':self.saved.pk})
        self.assertIsNone(page.context['just_saved_request'])
        self.assertNotContains(page, f'data-request-id="{self.saved.pk}"')
