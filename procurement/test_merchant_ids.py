import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from .importing import import_competitors
from .models import Offer, Supplier


class MerchantIdentifierTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('merchant-test')
        self.supplier = Supplier.objects.create(name='Компания Вальтек')
        self.offer = Offer.objects.create(supplier=self.supplier, brand='MANN', article='W67/1', name='Filter', cost=1000,
            kaspi_url='https://kaspi.kz/shop/p/filter-123456789/')

    def payload(self, merchant, url=None):
        content = {'checked_on': timezone.localdate().isoformat(), 'offers': [{'brand': self.offer.brand, 'article': self.offer.article,
            'source_url': self.offer.kaspi_url, 'city': '750000000', 'complete': True,
            'sellers': [{'merchant_id': merchant, 'name': 'Store', 'price': '2500.00', 'rating': 4.9, 'reviews': 25,
                         'url': url or f'https://kaspi.kz/shop/m/{merchant}/products?productCode=123456789'}]}]}
        return SimpleUploadedFile('sellers.json', json.dumps(content).encode())

    def test_official_named_and_numeric_merchant_ids_are_preserved(self):
        for merchant in ('Tanauto', 'KulanOil', 'Expertoil', '1234567'):
            self.assertEqual(import_competitors(self.payload(merchant), self.supplier.name, timezone.localdate(), self.user), 1)
            self.offer.refresh_from_db()
            self.assertEqual(self.offer.kaspi_sellers.sellers[0]['merchant_id'], merchant)

    def test_invalid_ids_and_different_store_urls_still_fail(self):
        for merchant in ('../other', 'Store Name', '', 'a' * 101):
            with self.assertRaises(ValueError):
                import_competitors(self.payload(merchant), self.supplier.name, timezone.localdate(), self.user)
        with self.assertRaises(ValueError):
            import_competitors(self.payload('Tanauto', 'https://kaspi.kz/shop/m/Other/products'), self.supplier.name, timezone.localdate(), self.user)
