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
        self.assertEqual([row['offer'].pk for row in rows], [self.priced.pk])
        self.assertEqual(calculator.call_count, 1)

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
        self.assertLessEqual(len(queries), 25)
        self.assertContains(response, 'Нет предложений на дату проверки')
