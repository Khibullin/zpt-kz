from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .analysis import evaluate
from .forms import SelectionFilterForm
from .models import Supplier, Offer, VehicleFitment, Preferences, Membership, KaspiSellerSnapshot
from .views import filtered_rows


class SelectionPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('performance-buyer')
        Membership.objects.create(user=cls.user)
        cls.prefs = Preferences.objects.create(user=cls.user, commission=10, expenses=250)
        cls.supplier = Supplier.objects.create(name='Performance supplier')
        cls.offers = Offer.objects.bulk_create([
            Offer(supplier=cls.supplier, brand='B', article=str(i), name=f'Offer {i}', cost=100,
                  stock='5', match_verified=True, kaspi_url=f'https://kaspi.kz/shop/p/test-{i+1}/',
                  description='Large detail '*1000, characteristics={'detail': 'x'*10000}) for i in range(1000)])
        VehicleFitment.objects.bulk_create([
            VehicleFitment(offer=cls.offers[i], make=f'Make {i}', model=f'Model {i}', verified=True)
            for i in range(60)])
        cls.priced = cls.offers[0]
        cls.priced.kaspi_price = 2000
        cls.priced.save(update_fields=['kaspi_price'])
        KaspiSellerSnapshot.objects.create(offer=cls.priced, source_url=cls.priced.kaspi_url,
            checked_on=timezone.localdate(), complete=True, sellers=[])

    def request(self, mode):
        request = RequestFactory().get('/', {'mode': mode})
        request.user = self.user
        return request

    def test_filter_options_do_not_issue_one_query_per_make(self):
        with CaptureQueriesContext(connection) as queries:
            form = SelectionFilterForm({'supplier': self.supplier.pk, 'vehicle_make': 'Make 0'})
        self.assertEqual(len(form.vehicle_models), 60)
        self.assertEqual(form.fields['vehicle_model'].choices[-1], ('Model 0', 'Model 0'))
        self.assertLessEqual(len(queries), 3)

    def test_best_does_not_evaluate_unpriced_catalogue(self):
        with patch('procurement.views.evaluate', wraps=evaluate) as calculator:
            rows = filtered_rows(self.request('best'), self.prefs, {})
        self.assertEqual(rows, [])  # Price alone does not qualify without confirmed sales.
        self.assertEqual(calculator.call_count, 0)  # Stale/undated prices are excluded in SQL.

    def test_no_sellers_does_not_evaluate_unchecked_catalogue(self):
        with patch('procurement.views.evaluate', wraps=evaluate) as calculator:
            rows = filtered_rows(self.request('no_sellers'), self.prefs, {})
        self.assertEqual([row['offer'].pk for row in rows], [self.priced.pk])
        self.assertEqual(calculator.call_count, 1)

    def test_page_renders_without_queries_for_each_seller_or_hidden_detail(self):
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('procurement:dashboard'), {'supplier': self.supplier.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1000)
        # Baseline includes the shared office membership lookup.
        self.assertLessEqual(len(queries), 26)
        self.assertContains(response, 'Нет предложений на дату проверки')

    def test_catalogue_paginates_before_calculating_full_rows(self):
        self.client.force_login(self.user)
        with patch('procurement.views.evaluate', wraps=evaluate) as calculator:
            response = self.client.get(reverse('procurement:dashboard'), {'supplier': self.supplier.pk})
        self.assertEqual(response.context['total'], 1000)
        self.assertEqual(calculator.call_count, 40)
        self.assertEqual(len(response.context['page']), 40)

    def test_sql_pages_preserve_reference_order_counts_and_filters(self):
        from decimal import Decimal
        for index, (price, cost) in enumerate([(501, 101), (500, 100), (300, 211), (50, 100), (2000, 0)]):
            offer = self.offers[index + 1]
            offer.kaspi_price = Decimal(price)
            offer.cost = Decimal(cost)
            offer.save(update_fields=['kaspi_price', 'cost'])
        self.client.force_login(self.user)
        for mode, sort in [('roi', ''), ('roi', 'profit'), ('reviewed', '')]:
            request = self.request(mode)
            filters = {'supplier': self.supplier, 'sort': sort}
            expected = filtered_rows(request, self.prefs, filters)
            for page_number in (1, 2, 25):
                response = self.client.get(reverse('procurement:dashboard'),
                    {'supplier': self.supplier.pk, 'mode': mode, 'sort': sort, 'page': page_number})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['total'], len(expected))
                self.assertEqual([r['offer'].pk for r in response.context['page']],
                                 [r['offer'].pk for r in expected[(page_number-1)*40:page_number*40]])
            response = self.client.get(reverse('procurement:dashboard'),
                {'supplier': self.supplier.pk, 'mode': mode, 'sort': sort, 'kaspi_status': 'empty'})
            self.assertEqual(response.context['total'], 1)
            self.assertEqual(response.context['page'][0]['offer'].pk, self.priced.pk)

    @patch('procurement.filter_options.options_cache_allowed', return_value=True)
    def test_filter_dictionary_reused_and_invalidated_on_change(self, allowed):
        SelectionFilterForm({'supplier': self.supplier.pk})
        with CaptureQueriesContext(connection) as queries:
            SelectionFilterForm({'supplier': self.supplier.pk})
        self.assertEqual(len(queries), 0)
        VehicleFitment.objects.create(offer=self.offers[0], make='NEW', model='NEW MODEL', verified=True)
        form = SelectionFilterForm({'supplier': self.supplier.pk, 'vehicle_make': 'NEW'})
        self.assertEqual(form.fields['vehicle_model'].choices[-1], ('NEW MODEL', 'NEW MODEL'))

    @patch('procurement.filter_options.options_cache_allowed', return_value=True)
    def test_price_and_photo_updates_keep_cached_dictionaries(self, allowed):
        from .filter_options import cache, REVISION_KEY
        SelectionFilterForm({'supplier': self.supplier.pk})
        revision = cache.get(REVISION_KEY)
        self.priced.photo_url = 'https://example.com/exact-part.jpg'
        self.priced.kaspi_price = 2500
        self.priced.save(update_fields=['photo_url', 'kaspi_price'])
        self.assertEqual(cache.get(REVISION_KEY), revision)
        with CaptureQueriesContext(connection) as queries:
            SelectionFilterForm({'supplier': self.supplier.pk})
        self.assertEqual(len(queries), 0)

    @patch('procurement.filter_options.options_cache_allowed', return_value=True)
    def test_generations_and_years_cached_without_losing_supplier_scope(self, allowed):
        fitment = self.offers[0].fitments.get()
        fitment.configurations = [{'generation': 'G1', 'years': '2010–2012'}]
        fitment.save(update_fields=['configurations'])
        params = {'supplier': self.supplier.pk, 'vehicle_make': 'Make 0', 'vehicle_model': 'Model 0'}
        form = SelectionFilterForm(params)
        self.assertEqual(form.fields['vehicle_generation'].choices[-1], ('G1', 'G1'))
        self.assertEqual(form.fields['vehicle_years'].choices, [('2012', '2012'), ('2011', '2011'), ('2010', '2010')])
        with CaptureQueriesContext(connection) as queries:
            SelectionFilterForm(params)
        self.assertEqual(len(queries), 0)
        other = Supplier.objects.create(name='Other supplier')
        self.assertEqual(SelectionFilterForm({'supplier': other.pk}).fields['vehicle_years'].choices, [])
        fitment.configurations = [{'generation': 'G2', 'years': '2014'}]
        fitment.save(update_fields=['configurations'])
        form = SelectionFilterForm(params)
        self.assertEqual(form.fields['vehicle_generation'].choices[-1], ('G2', 'G2'))
        self.assertEqual(form.fields['vehicle_years'].choices, [('2014', '2014')])

    def test_list_limits_fitments_but_detail_retains_all(self):
        VehicleFitment.objects.bulk_create([
            VehicleFitment(offer=self.offers[0], make='EXTRA', model=f'Model {i}', verified=True)
            for i in range(20)])
        self.client.force_login(self.user)
        response = self.client.get(reverse('procurement:dashboard'), {'supplier': self.supplier.pk})
        offer = next(row['offer'] for row in response.context['page'] if row['offer'].pk == self.offers[0].pk)
        self.assertEqual(len(offer.compact_fitments), 4)
        self.assertContains(response, 'Все модели и модификации')
        detail = self.client.get(reverse('procurement:detail', args=[offer.pk]))
        self.assertEqual(detail.context['offer'].fitments.count(), 21)
