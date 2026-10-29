"""Manual negative reviews must not be promoted by identical display titles."""
from datetime import date
from io import StringIO
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from .importing import import_matches
from .models import Activity, Offer, Supplier


class IdentityReviewImportTests(TestCase):
    def setUp(self):
        self.day = date(2026, 10, 30)
        self.user = get_user_model().objects.create_user('identity-reviewer')
        self.supplier = Supplier.objects.create(name='Omega')
        self.offer = Offer.objects.create(supplier=self.supplier, brand='WXQP', article='321893',
            name='Генератор WXQP 321893', cost=1000, stock='12', min_order=2,
            kaspi_price=3000, kaspi_url='https://kaspi.kz/shop/p/generator-321893-123456/',
            sales_30=8, characteristics={'other': 'preserve'})
        self.row = dict(brand='WXQP', article='321893', source_url=self.offer.kaspi_url,
            source_date=self.day.isoformat(), title=self.offer.name,
            kaspi_article='321893', kaspi_brand='WXQP', name_reviewed=False)

    def run_import(self, rows=None, day=None):
        day = day or self.day
        return import_matches(StringIO(json.dumps({'checked_on': day.isoformat(),
            'offers': rows or [self.row]})), 'Omega', day, self.user)

    def test_explicit_negative_wins_over_exact_title_and_is_audited(self):
        self.offer.match_verified = True
        self.offer.save(update_fields=['match_verified'])
        self.row.update(review_decision='requires_review', review_reason='Omega 65A/14V, Kaspi 90A/12V')
        self.assertEqual(self.run_import(), 1)
        self.offer.refresh_from_db()
        self.assertFalse(self.offer.match_verified)
        evidence = self.offer.characteristics['kaspi_identity']
        self.assertFalse(evidence['confirmed'])
        self.assertEqual(evidence['title'], self.row['title'])
        self.assertEqual(evidence['review_reason'], self.row['review_reason'])
        self.assertEqual(evidence['review_decision'], 'requires_review')
        self.assertEqual(Activity.objects.get(action='match_checked').details['review_reason'], self.row['review_reason'])
        self.assertEqual(self.offer.characteristics['other'], 'preserve')
        self.assertEqual((self.offer.cost, self.offer.kaspi_price, self.offer.stock, self.offer.min_order, self.offer.sales_30),
                         (1000, 3000, '12', 2, 8))

    def test_legacy_exact_match_is_unchanged(self):
        self.run_import()
        self.offer.refresh_from_db()
        self.assertTrue(self.offer.match_verified)
        self.assertNotIn('review_decision', self.offer.characteristics['kaspi_identity'])

    def test_unknown_or_incomplete_decisions_are_rejected(self):
        for fields in [dict(review_decision='confirmed'),
                       dict(review_decision='requires_review'),
                       dict(review_decision='requires_review', review_reason=' '),
                       dict(review_decision='requires_review', review_reason='x' * 2001),
                       dict(review_decision='requires_review', review_reason='conflict', name_reviewed=True)]:
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    self.run_import([{**self.row, **fields}])
        self.assertFalse(Activity.objects.exists())

    def test_negative_does_not_bypass_current_card_url(self):
        self.row.update(source_url='https://kaspi.kz/shop/p/other-123457/',
            review_decision='requires_review', review_reason='conflict')
        with self.assertRaises(ValueError):
            self.run_import()
        self.assertFalse(Activity.objects.exists())

    def test_older_source_or_checked_date_cannot_overwrite_newer_review(self):
        self.offer.characteristics['kaspi_identity'] = dict(source_url=self.offer.kaspi_url,
            source_date='2026-10-30', checked_on='2026-10-30', confirmed=True)
        self.offer.match_verified = True
        self.offer.save()
        for source_date, day in [('2026-10-29', self.day), ('2026-10-29', date(2026, 10, 29))]:
            with self.subTest(source_date=source_date, day=day):
                with self.assertRaises(ValueError):
                    self.run_import([{**self.row, 'source_date': source_date}], day=day)
        self.offer.refresh_from_db()
        self.assertTrue(self.offer.match_verified)
        self.assertEqual(self.offer.characteristics['kaspi_identity']['checked_on'], '2026-10-30')

    def test_invalid_second_row_rolls_back_first_review_and_activity(self):
        good = {**self.row, 'review_decision': 'requires_review', 'review_reason': 'conflict'}
        other = Offer.objects.create(supplier=self.supplier, brand='WXQP', article='other',
            name='Other', cost=1, kaspi_url='https://kaspi.kz/shop/p/other-123457/')
        bad = {**self.row, 'article': other.article, 'source_url': other.kaspi_url,
               'review_decision': 'invalid'}
        with self.assertRaises(ValueError):
            self.run_import([good, bad])
        self.offer.refresh_from_db()
        self.assertNotIn('kaspi_identity', self.offer.characteristics)
        self.assertFalse(Activity.objects.exists())

    def test_reviewed_name_cannot_override_article_or_brand_mismatch(self):
        for field in ('kaspi_article', 'kaspi_brand'):
            with self.subTest(field=field):
                self.run_import([{**self.row, field: 'different', 'name_reviewed': True}])
                self.offer.refresh_from_db()
                self.assertFalse(self.offer.match_verified)
