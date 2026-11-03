from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .fitment_filters import configuration_years
from .models import Membership, Supplier, Offer, Preferences, KaspiSellerSnapshot, VehicleFitment


class BestFiltersTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('best-filters')
        Membership.objects.create(user=self.user, role='admin')
        Preferences.objects.create(user=self.user, commission=10, expenses=250)
        self.supplier = Supplier.objects.create(name='Filter supplier')
        self.client.force_login(self.user)
        self.url = reverse('procurement:dashboard')
        for article, model in [('CAM', 'CAMRY'), ('COR', 'COROLLA')]:
            offer = Offer.objects.create(supplier=self.supplier, brand='PART', article=article,
                name=model + ' filter', category='Фильтры', cost=100, kaspi_price=5000,
                stock='5', match_verified=True, checked_on=timezone.localdate(),
                sales_30=2, evidence_date=timezone.localdate(), kaspi_url=f'https://kaspi.kz/shop/p/{article}-123/')
            KaspiSellerSnapshot.objects.create(offer=offer, source_url=offer.kaspi_url,
                checked_on=timezone.localdate(), complete=True, sellers=[{'name': 'Shop', 'price': '5000'}])
            VehicleFitment.objects.create(offer=offer, make='TOYOTA', model=model, verified=True,
                configurations=[{'generation': 'XV40', 'years': '2006–2011'},
                                {'generation': 'XV50', 'years': '2012–2017'}])

    def get(self, **params):
        return self.client.get(self.url, {'supplier': self.supplier.pk, 'mode': 'best', **params})

    def test_best_has_shared_filters_without_kaspi_controls(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        for field in ('q', 'brand', 'category', 'vehicle_make', 'vehicle_model', 'vehicle_generation', 'vehicle_year'):
            self.assertContains(response, f'id="id_{field}"')
        self.assertNotContains(response, 'id="id_kaspi_status"')
        self.assertNotContains(response, 'id="id_verified"')
        self.assertEqual(response.context['total'], 2)

    def test_best_filters_and_csv_keep_same_scope(self):
        params = {'vehicle_make': 'TOYOTA', 'vehicle_model': 'CAMRY',
                  'vehicle_generation': 'XV40', 'vehicle_year': '2010', 'q': 'CAM', 'brand': 'PART', 'category': 'Фильтры'}
        response = self.get(**params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)
        self.assertContains(response, 'CAMRY filter')
        self.assertNotContains(response, 'COROLLA filter')
        csv = self.get(**params, download='csv').content.decode('utf-8-sig')
        self.assertIn('CAMRY filter', csv)
        self.assertNotIn('COROLLA filter', csv)
        self.assertEqual(response.context['reset_url'], f'{self.url}?supplier={self.supplier.pk}&mode=best')

    def test_year_and_generation_must_match_same_configuration(self):
        response = self.get(vehicle_make='TOYOTA', vehicle_model='CAMRY', vehicle_generation='XV40', vehicle_year='2015')
        self.assertEqual(response.status_code, 400)
        response = self.get(vehicle_make='TOYOTA', vehicle_model='CAMRY', vehicle_year='2015')
        self.assertEqual(response.context['total'], 1)

    def test_all_catalog_uses_same_vehicle_filters(self):
        response = self.get(mode='roi', vehicle_make='TOYOTA', vehicle_model='COROLLA', vehicle_year='2010')
        self.assertEqual(response.context['total'], 1)
        self.assertContains(response, 'COROLLA filter')

    def test_kaspi_url_filters_do_not_empty_best(self):
        response = self.get(kaspi_status='missing', verified='on', sort='profit')
        self.assertEqual(response.context['total'], 2)

    def test_explicit_years_only(self):
        self.assertEqual(list(configuration_years('2006–2011')), list(range(2006, 2012)))
        self.assertEqual(list(configuration_years('2010')), [2010])
        self.assertEqual(list(configuration_years('неизвестно')), [])
