"""Strict profitability cutoff before Omega Apify candidate storage."""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .apify_buying_import import import_apify_results
from .models import KaspiBuyingCandidate, Offer, Preferences, Supplier


class ApifyRoiGateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='roi_gate_operator', password='test-only')
        self.omega = Supplier.objects.create(name='Omega')
        self.other = Supplier.objects.create(name='Phaeton')
        Preferences.objects.create(user=self.user, commission=Decimal('10'), expenses=Decimal('250'))
        Offer.objects.create(supplier=self.omega, brand='TESTBRAND', article='AB-101',
                             name='Амортизатор', cost=Decimal('6000'), stock='5')
        Offer.objects.create(supplier=self.omega, brand='TESTBRAND', article='AB-102',
                             name='Амортизатор', cost=Decimal('6000'), stock='5')
        Offer.objects.create(supplier=self.omega, brand='TESTBRAND', article='AB-103',
                             name='Амортизатор', cost=Decimal('6000'), stock='0')
        Offer.objects.create(supplier=self.other, brand='TESTBRAND', article='AB-104',
                             name='Амортизатор', cost=Decimal('6000'), stock='5')

    def test_rejects_49_percent_and_accepts_exactly_50_percent(self):
        # (price * 0.9 - 6000 - 250) / 6000 = 50% when price=10277.777...
        # Prices are stored at two decimal places; 10277.78 is just above 50%.
        rows = [
            {'id': '123456789', 'brand': 'TESTBRAND', 'title': 'Амортизатор AB-101',
             'minPrice': '10277.78'},
            {'id': '123456790', 'brand': 'TESTBRAND', 'title': 'Амортизатор AB-102',
             'minPrice': '10277.77'},
            {'id': '123456791', 'brand': 'TESTBRAND', 'title': 'Амортизатор AB-103',
             'minPrice': '20000'},
            {'id': '123456792', 'brand': 'TESTBRAND', 'title': 'Амортизатор AB-104',
             'minPrice': '20000'},
        ]
        upload = SimpleUploadedFile('apify.json', json.dumps(rows).encode('utf8'))
        result = import_apify_results(upload, upload.name, user=self.user)
        self.assertEqual(result['rejected_below_50'], 1)
        self.assertEqual(
            set(KaspiBuyingCandidate.objects.values_list('product_id', flat=True)),
            {'123456789'},
        )
        self.assertEqual(KaspiBuyingCandidate.objects.first().offer.supplier, self.omega)

    def test_missing_expenses_does_not_import(self):
        Preferences.objects.filter(user=self.user).update(expenses=None)
        upload = SimpleUploadedFile('apify.json', json.dumps([
            {'id': '123456793', 'brand': 'TESTBRAND',
             'title': 'Амортизатор AB-101', 'price': '20000'},
        ]).encode('utf8'))
        import_apify_results(upload, upload.name, user=self.user)
        self.assertEqual(KaspiBuyingCandidate.objects.count(), 0)
