import json

from django.test import Client, TestCase

from catalog.models import Brand as CatalogBrand
from catalog.models import CarModel as CatalogCarModel
from catalog.models import Country as CatalogCountry
from core.models import Brand as CoreBrand
from core.models import CarModel as CoreCarModel
from core.models import Country as CoreCountry
from core.vehicle_catalog import VEHICLE_CATALOG
from core.vehicle_catalog_sync import sync_vehicle_catalog


class VehicleCatalogLandRoverTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_land_rover_present_in_vehicle_catalog(self):
        europe = VEHICLE_CATALOG['Европа']
        self.assertIn('Land Rover', europe)
        self.assertEqual(europe['Land Rover'], ['Range Rover'])

    def test_sync_creates_land_rover_and_range_rover_in_core(self):
        stats = sync_vehicle_catalog(transport_type='car')

        europe = CoreCountry.objects.get(name='Европа')
        lr = CoreBrand.objects.get(
            country=europe,
            name='Land Rover',
            transport_type='car',
        )
        rr = CoreCarModel.objects.get(
            brand=lr,
            name='Range Rover',
            transport_type='car',
        )

        self.assertGreaterEqual(stats['core']['brands'], 1)
        self.assertGreaterEqual(stats['core']['models'], 1)
        self.assertEqual(lr.transport_type, 'car')
        self.assertEqual(rr.transport_type, 'car')

    def test_resync_does_not_create_duplicates(self):
        sync_vehicle_catalog(transport_type='car')
        stats = sync_vehicle_catalog(transport_type='car')

        self.assertEqual(stats['core']['brands'], 0)
        self.assertEqual(stats['core']['models'], 0)

        europe = CoreCountry.objects.get(name='Европа')
        self.assertEqual(
            CoreBrand.objects.filter(
                country=europe,
                name='Land Rover',
                transport_type='car',
            ).count(),
            1,
        )
        lr = CoreBrand.objects.get(
            country=europe,
            name='Land Rover',
            transport_type='car',
        )
        self.assertEqual(
            CoreCarModel.objects.filter(
                brand=lr,
                name='Range Rover',
                transport_type='car',
            ).count(),
            1,
        )

    def test_sync_reuses_existing_catalog_land_rover(self):
        europe, _ = CatalogCountry.objects.get_or_create(name='Европа')
        existing_brand = CatalogBrand.objects.create(
            country=europe,
            name='Land Rover',
        )
        existing_model = CatalogCarModel.objects.create(
            brand=existing_brand,
            name='Range Rover',
        )

        sync_vehicle_catalog(transport_type='car')

        self.assertEqual(
            CatalogBrand.objects.filter(
                country=europe,
                name='Land Rover',
            ).count(),
            1,
        )
        self.assertEqual(
            CatalogCarModel.objects.filter(
                brand=existing_brand,
                name='Range Rover',
            ).count(),
            1,
        )
        self.assertEqual(existing_brand.id, CatalogBrand.objects.get(
            country=europe,
            name='Land Rover',
        ).id)
        self.assertEqual(existing_model.id, CatalogCarModel.objects.get(
            brand=existing_brand,
            name='Range Rover',
        ).id)

    def test_api_returns_land_rover_only_for_car_transport_type(self):
        sync_vehicle_catalog(transport_type='car')

        europe = CoreCountry.objects.get(name='Европа')
        lr = CoreBrand.objects.get(
            country=europe,
            name='Land Rover',
            transport_type='car',
        )
        CoreBrand.objects.create(
            country=europe,
            name='Land Rover',
            transport_type='truck',
        )

        car_response = self.client.get(
            '/api/brands-by-country/',
            {'country_id': europe.id, 'transport_type': 'car'},
        )
        truck_response = self.client.get(
            '/api/brands-by-country/',
            {'country_id': europe.id, 'transport_type': 'truck'},
        )

        car_brands = json.loads(car_response.content)
        truck_brands = json.loads(truck_response.content)

        car_lr = [b for b in car_brands if b['name'] == 'Land Rover']
        truck_lr = [b for b in truck_brands if b['name'] == 'Land Rover']

        self.assertEqual(len(car_lr), 1)
        self.assertEqual(car_lr[0]['transport_type'], 'car')
        self.assertEqual(len(truck_lr), 1)
        self.assertEqual(truck_lr[0]['transport_type'], 'truck')
        self.assertNotEqual(car_lr[0]['id'], truck_lr[0]['id'])

        models_response = self.client.get(
            '/api/models-by-brand/',
            {'brand_id': lr.id, 'transport_type': 'car'},
        )
        models = json.loads(models_response.content)
        rr = [m for m in models if m['name'] == 'Range Rover']

        self.assertEqual(len(rr), 1)
        self.assertEqual(rr[0]['transport_type'], 'car')
