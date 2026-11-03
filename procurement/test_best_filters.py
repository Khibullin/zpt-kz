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
        for field in ('q', 'brand', 'category', 'vehicle_make', 'vehicle_model', 'vehicle_generation', 'year_mode'):
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
        self.assertEqual(response.context['total'], 0)
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

    def test_independent_year_and_seller_count(self):
        for mode in ('roi', 'best'):
            self.assertEqual(self.get(mode=mode, vehicle_year='2010').context['total'], 2)
            self.assertEqual(self.get(mode=mode, kaspi_seller_count='1').context['total'], 2)
            self.assertEqual(self.get(mode=mode, kaspi_seller_count='2').context['total'], 0)
            self.assertEqual(self.get(mode=mode, vehicle_year='2010', kaspi_seller_count='1').context['total'], 2)
        snapshot = KaspiSellerSnapshot.objects.first()
        snapshot.complete = False
        snapshot.save()
        self.assertEqual(self.get(kaspi_seller_count='1').context['total'], 1)

    def test_zero_excludes_unknown_and_export_keeps_count(self):
        snapshot = KaspiSellerSnapshot.objects.first()
        snapshot.sellers = []
        snapshot.save()
        response = self.get(mode='roi', kaspi_seller_count='0')
        self.assertEqual(response.context['total'], 1)
        csv = self.get(mode='roi', kaspi_seller_count='0', download='csv').content.decode('utf-8-sig')
        self.assertIn(snapshot.offer.name, csv)
        snapshot.complete = False
        snapshot.save()
        self.assertEqual(self.get(mode='roi', kaspi_seller_count='0').context['total'], 0)

    def test_multiple_years_and_range_in_both_tabs(self):
        for mode in ('roi', 'best'):
            self.assertEqual(self.get(mode=mode, year_mode='selected', vehicle_years=['2007', '2015']).context['total'], 2)
            self.assertEqual(self.get(mode=mode, year_mode='range', year_from='2007', year_to='2015').context['total'], 2)
            self.assertEqual(self.get(mode=mode, year_mode='range', year_from='2015', year_to='2007').status_code, 400)
            self.assertEqual(self.get(mode=mode, year_mode='selected').status_code, 400)
            self.assertEqual(self.get(mode=mode, year_mode='', vehicle_years=['2007']).context['total'], 2)
        response = self.get(year_mode='selected', vehicle_years=['2007', '2015'], vehicle_make='TOYOTA', kaspi_seller_count='1')
        self.assertEqual(response.context['total'], 2)
        self.assertContains(response, 'vehicle_years=2007&amp;vehicle_years=2015')

    def test_interval_matches_any_year_but_same_generation(self):
        self.assertEqual(self.get(vehicle_make='TOYOTA', vehicle_model='CAMRY', vehicle_generation='XV40',
            year_mode='range', year_from='2012', year_to='2015').context['total'], 0)
        self.assertEqual(self.get(year_mode='selected', vehicle_years=['2007', '2015'], download='csv').status_code, 200)

    def test_seller_intervals_include_boundaries_and_exclude_unknown(self):
        snapshots = list(KaspiSellerSnapshot.objects.order_by('pk'))
        snapshots[0].sellers = []
        snapshots[0].save()
        snapshots[1].sellers = [{'name': str(i), 'price': '5000'} for i in range(3)]
        snapshots[1].save()
        self.assertEqual(self.get(mode='roi', seller_mode='max', seller_max='3').context['total'], 2)
        self.assertEqual(self.get(mode='roi', seller_mode='max', seller_max='2').context['total'], 1)
        for mode in ('roi', 'best'):
            self.assertEqual(self.get(mode=mode, seller_mode='range', seller_min='2', seller_max='3').context['total'], 1)
            self.assertEqual(self.get(mode=mode, seller_mode='exact', kaspi_seller_count='3').context['total'], 1)
            self.assertEqual(self.get(mode=mode, seller_mode='range', seller_min='4', seller_max='2').status_code, 400)
            self.assertEqual(self.get(mode=mode, seller_mode='max').status_code, 400)
        snapshots[1].complete = False
        snapshots[1].save()
        self.assertEqual(self.get(mode='roi', seller_mode='max', seller_max='3').context['total'], 1)
        self.assertEqual(self.get(mode='roi', seller_mode='', kaspi_seller_count='4').context['total'], 2)
        csv = self.get(mode='roi', seller_mode='max', seller_max='0', download='csv').content.decode('utf-8-sig')
        self.assertIn(snapshots[0].offer.name, csv)
        self.assertNotIn(snapshots[1].offer.name, csv)
