from django.test import TestCase

from core.models import Brand, CarModel, Country


class HomeVehiclePickerTests(TestCase):
    def setUp(self):
        japan = Country.objects.create(name='Япония')
        belarus = Country.objects.create(name='Беларусь')

        self.mazda = Brand.objects.create(
            country=japan,
            name='Mazda',
            transport_type='car',
        )
        CarModel.objects.create(
            brand=self.mazda,
            name='CX-5',
            transport_type='car',
        )

        self.maz = Brand.objects.create(
            country=belarus,
            name='МАЗ',
            transport_type='truck',
        )
        CarModel.objects.create(
            brand=self.maz,
            name='5551',
            transport_type='truck',
        )

    def test_cyrillic_mazd_prefix_returns_mazda_before_maz(self):
        response = self.client.get(
            '/api/vehicle-suggest/',
            {'kind': 'brand', 'q': 'мазд'},
        )

        self.assertEqual(response.status_code, 200)
        items = response.json()['items']
        self.assertGreaterEqual(len(items), 1)
        self.assertEqual(items[0]['name'], 'Mazda')
        self.assertEqual(items[0]['transport_type'], 'car')

    def test_model_suggestions_are_limited_to_selected_brand(self):
        response = self.client.get(
            '/api/vehicle-suggest/',
            {
                'kind': 'model',
                'q': 'cx',
                'brand_id': str(self.mazda.id),
                'brand': 'Mazda',
                'transport_type': 'car',
            },
        )

        self.assertEqual(response.status_code, 200)
        items = response.json()['items']
        self.assertEqual([item['name'] for item in items], ['CX-5'])
        self.assertTrue(all(item['brand_id'] == self.mazda.id for item in items))

    def test_home_page_uses_v8_picker_and_keeps_model_disabled_initially(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'home-parts-form-v8.js')
        self.assertContains(response, 'id="home-vehicle-picker"')
        self.assertContains(response, 'id="home-brand-open"')
        self.assertContains(response, 'id="home-model-open"')
        self.assertContains(response, 'placeholder="Выберите марку"')
        self.assertContains(response, 'placeholder="Сначала выберите марку"')
