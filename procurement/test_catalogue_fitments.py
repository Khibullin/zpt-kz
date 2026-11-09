import gzip
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.template.loader import render_to_string
from django.test import TestCase

from .fitment_filters import configuration_years, matches_configuration
from .models import Offer, Supplier, VehicleFitment


class CatalogueBackfillTests(TestCase):
    def setUp(self):
        self.migration = importlib.import_module('procurement.migrations.0018_phaeton_catalogue_fitments')
        self.supplier = Supplier.objects.create(name='Фаетон')
        self.offer = Offer.objects.create(supplier=self.supplier, brand='AYWIPARTS', article='AW1',
            name='Рычаг', cost=1000, stock='5', match_verified=True, kaspi_price=5000,
            characteristics={'kaspi_identity': {'confirmed': True}})

    def entry(self, **kwargs):
        return {'brand': 'AYWIPARTS', 'article': 'AW1', 'source_article': 'AW1',
            'source_url': 'https://aywiparts.ru/product/view/123', 'manufacturer': 'AYWIPARTS',
            'checked_on': '2026-11-09', 'status': 'sourced', 'note': 'Manufacturer catalogue',
            'rows': [{'make': 'TOYOTA', 'model': 'COROLLA', 'years': '2001 - 2007',
                      'engine': '1.6 VVT-i', 'notes': 'Only specified engine'}], **kwargs}

    def run_import(self, entries):
        data = gzip.compress(json.dumps({'offers': entries}).encode())
        with patch.object(self.migration.Path, 'read_bytes', return_value=data):
            self.migration.load_fitments(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))

    def test_repeat_import_preserves_commercial_data_and_other_suppliers(self):
        other = Offer.objects.create(supplier=Supplier.objects.create(name='Other'), brand='AYWIPARTS',
                                     article='AW1', name='Рычаг', cost=700)
        self.run_import([self.entry()]); self.run_import([self.entry()])
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.cost, 1000)
        self.assertEqual(self.offer.kaspi_price, 5000)
        self.assertEqual(self.offer.stock, '5')
        self.assertTrue(self.offer.match_verified)
        self.assertTrue(self.offer.characteristics['kaspi_identity']['confirmed'])
        fit = self.offer.fitments.get()
        self.assertTrue(fit.verified)
        self.assertTrue(matches_configuration(fit.configurations, years=[2007]))
        self.assertFalse(matches_configuration(fit.configurations, years=[2008]))
        self.assertFalse(other.fitments.exists())
        html = render_to_string('procurement/fitments.html', {'offer': self.offer, 'fitments': [fit]})
        self.assertIn('Only specified engine', html)

    def test_ambiguous_text_remains_unverified(self):
        self.run_import([self.entry(rows=[], status='text_needs_review',
                                   raw_applicability='TOYOTA HIGHLANDER / LEXUS RX')])
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.applicability, 'TOYOTA HIGHLANDER / LEXUS RX')
        self.assertFalse(self.offer.fitments.exists())
        self.assertEqual(self.offer.characteristics['manufacturer_applicability']['status'], 'text_needs_review')

    def test_existing_reviewed_pairs_and_text_are_not_overwritten(self):
        self.offer.applicability = 'Reviewed restrictions'
        self.offer.save()
        fit = VehicleFitment.objects.create(offer=self.offer, make='TOYOTA', model='COROLLA',
            verified=True, source_url='https://example.com/review', configurations=[{'years': '2005'}])
        self.run_import([self.entry()])
        fit.refresh_from_db(); self.offer.refresh_from_db()
        self.assertEqual(fit.configurations, [{'years': '2005'}])
        self.assertEqual(fit.source_url, 'https://example.com/review')
        self.assertEqual(self.offer.applicability, 'Reviewed restrictions')

    def test_article_suffix_and_source_host_are_required(self):
        with self.assertRaises(ValueError): self.run_import([self.entry(source_article='AW1R')])
        with self.assertRaises(ValueError): self.run_import([self.entry(source_url='https://example.com/AW1')])
        with self.assertRaises(ValueError): self.run_import([self.entry(status='text_needs_review')])
        self.assertFalse(self.offer.fitments.exists())

    def test_open_russian_catalogue_years_are_filterable(self):
        self.assertIn(2004, configuration_years('2002 - по сегодняшний день'))
        self.assertNotIn(2001, configuration_years('2002 - по сегодняшний день'))

    def test_frozen_catalogue_source_integrity(self):
        path = Path(__file__).parent / 'data' / 'phaeton_catalogue_fitments_20261109.json.gz'
        data = json.loads(gzip.decompress(path.read_bytes()))
        seen = set()
        for entry in data['offers']:
            self.migration.validate_entry(entry)
            key = (entry['brand'], entry['article'])
            self.assertNotIn(key, seen); seen.add(key)
            for row in entry['rows']:
                if entry['brand'] == 'NGK':
                    self.assertIn(entry['article'], row['source_row'])
                    self.assertFalse(row['make'] == 'KIA' and row['model'] == 'GALLARDO')
                    self.assertFalse(row['make'] == 'VAUXHALL' and 'GOLF' in row['model'])
                if entry['brand'] == 'AKEBONO':
                    self.assertNotEqual(row['model'], '(FCEV)')
                    self.assertFalse(row['make'] == 'TOYOTA' and row['model'].startswith('LEXUS'))
                    self.assertIn(row['source_article'], ' '.join(str(v or '') for v in row['source_row'][4:]))
                self.assertLessEqual(len(row['model']), 150)
