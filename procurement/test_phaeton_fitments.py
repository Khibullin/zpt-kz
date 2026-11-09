import importlib
import json
import gzip
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase

from .models import Offer, Supplier, VehicleFitment
from .fitment_filters import matches_configuration


class ManufacturerFitmentTests(TestCase):
    def test_backfill_preserves_prices_identity_and_supplier_boundaries(self):
        migration = importlib.import_module('procurement.migrations.0017_phaeton_manufacturer_fitments')
        supplier = Supplier.objects.create(name='Фаетон')
        offer = Offer.objects.create(supplier=supplier, brand='555', article='SA-1762R',
                                     name='555 Рычаг', cost=1234, stock='5', match_verified=True)
        other = Offer.objects.create(supplier=Supplier.objects.create(name='Other'), brand='555',
                                     article=offer.article, name=offer.name, cost=900)
        wrong = Offer.objects.create(supplier=supplier, brand='OTHER', article=offer.article, name='Рычаг', cost=800)
        payload = {'checked_on': '2026-11-09', 'offers': [{'article': 'SA-1762R', 'rows': [{
            'article': 'SA-1762R', 'make': 'MAZDA', 'model': 'MAZDA3', 'years': '2009 -',
            'chassis': 'BLEFP', 'position': 'LOW R',
            'source_url': 'https://www.sankei-555.com/search/detail.php?p_id=1'}]}]}
        with patch.object(migration.gzip, 'decompress', return_value=json.dumps(payload).encode()):
            migration.load_fitments(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
            migration.load_fitments(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        offer.refresh_from_db()
        self.assertEqual(offer.cost, 1234)
        self.assertEqual(offer.stock, '5')
        self.assertTrue(offer.match_verified)
        fit = offer.fitments.get()
        self.assertTrue(fit.verified)
        self.assertIn('BLEFP', offer.applicability)
        self.assertTrue(matches_configuration(fit.configurations, years=[2009]))
        self.assertFalse(matches_configuration(fit.configurations, years=[2008]))
        self.assertFalse(other.fitments.exists())
        self.assertFalse(wrong.fitments.exists())

    def test_catalogue_has_exact_articles_and_sourced_configurations(self):
        path = Path(__file__).parent / 'data' / 'phaeton_555_fitments_20261109.json.gz'
        payload = json.loads(gzip.decompress(path.read_bytes()))
        articles = set()
        for entry in payload['offers']:
            self.assertNotIn(entry['article'], articles)
            articles.add(entry['article'])
            for row in entry['rows']:
                self.assertEqual(row['article'], entry['article'])
                self.assertTrue(row['make'])
                self.assertTrue(row['model'])
                self.assertTrue(row['source_url'].startswith('https://www.sankei-555.com/search/detail.php?'))
