from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from core.models import Brand, Country
from .forms import SelectionFilterForm
from .models import Membership, Offer, Supplier, VehicleFitment


class VehicleCountryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.china = Country.objects.create(name='Китай')
        cls.japan = Country.objects.create(name='Япония')
        Brand.objects.create(name='Chery', country=cls.china)
        Brand.objects.create(name='Toyota', country=cls.japan)
        cls.supplier = Supplier.objects.create(name='Country supplier')
        cls.other = Supplier.objects.create(name='Other supplier')
        cls.user = get_user_model().objects.create_user('country-buyer')
        Membership.objects.create(user=cls.user)
        cls.chinese = cls.offer('Chinese part', 'CHERY', 'TIGGO')
        cls.japanese = cls.offer('Japanese part', 'TOYOTA', 'CAMRY')
        cls.unknown = cls.offer('Unknown fitment', 'CHERY', 'TIGGO', verified=False)
        cls.mixed = cls.offer('Mixed part', 'CHERY', 'TIGGO')
        VehicleFitment.objects.create(offer=cls.mixed, make='TOYOTA', model='CAMRY', verified=True)

    @classmethod
    def offer(cls, name, make, model, verified=True):
        offer = Offer.objects.create(supplier=cls.supplier, brand='Part manufacturer', article=name, name=name, cost=100)
        VehicleFitment.objects.create(offer=offer, make=make, model=model, verified=verified)
        return offer

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('procurement:dashboard')

    def results(self, **params):
        response = self.client.get(self.url, {'supplier': '', 'vehicle_country': self.china.pk, **params})
        self.assertEqual(response.status_code, 200)
        return response

    def test_country_uses_verified_vehicle_make_and_keeps_csv_scope(self):
        response = self.results()
        self.assertEqual({r['offer'].pk for r in response.context['page']}, {self.chinese.pk, self.mixed.pk})
        self.assertContains(response, 'Страна автомобиля')
        csv = self.results(download='csv').content.decode('utf-8-sig')
        self.assertIn('Chinese part', csv)
        self.assertNotIn('Japanese part', csv)
        self.assertNotIn('Unknown fitment', csv)

    def test_country_and_model_must_match_the_same_fitment(self):
        # CAMRY exists, but on a Japanese fitment of the same offer.
        response = self.client.get(self.url, {'supplier': '', 'vehicle_country': self.china.pk, 'vehicle_make': 'CHERY', 'vehicle_model': 'CAMRY'})
        self.assertEqual(response.status_code, 400)
        from .views import filtered_rows
        from .models import Preferences
        from django.test import RequestFactory
        request = RequestFactory().get(self.url)
        request.user = self.user
        preferences, _ = Preferences.objects.get_or_create(user=self.user)
        self.assertEqual(filtered_rows(request, preferences, {'vehicle_country': self.china, 'vehicle_model': 'CAMRY'}), [])

    def test_country_limits_makes_and_models_with_supplier(self):
        form = SelectionFilterForm({'supplier': self.supplier.pk, 'vehicle_country': self.china.pk, 'vehicle_make': 'CHERY'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(list(form.fields['vehicle_make'].choices), [('', 'Все марки'), ('CHERY', 'CHERY')])
        self.assertEqual(form.vehicle_models, {'CHERY': ['TIGGO']})
        empty = SelectionFilterForm({'supplier': self.other.pk, 'vehicle_country': self.china.pk})
        self.assertTrue(empty.is_valid(), empty.errors)
        self.assertEqual(len(empty.fields['vehicle_make'].choices), 1)

    def test_ambiguous_make_and_invalid_country_are_not_accepted(self):
        Brand.objects.create(name='CHERY', country=self.japan)
        self.assertEqual(self.results().context['total'], 0)
        response = self.client.get(self.url, {'supplier': '', 'vehicle_country': '999999'})
        self.assertEqual(response.status_code, 400)
