from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.db import connection
from django.db.models import Q
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import Membership, Offer, Supplier, VehicleFitment, WholesaleSelectionRule
from .selection_exports import filtered_offers
from .selection_rule_views import matching_years


class PreparationPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('preparation-performance')
        Membership.objects.create(user=cls.user, role='admin')
        cls.supplier = Supplier.objects.create(name='Omega')
        cls.rule = WholesaleSelectionRule.objects.create(supplier=cls.supplier)
        cls.year = str(date.today().year)
        cls.direct = Offer.objects.create(supplier=cls.supplier, brand='TEST', article='DIRECT',
            name='Амортизатор', cost=Decimal('2000'), stock='20>', applicability=cls.year,
            description='Large description ' * 100, characteristics={'large': 'x' * 1000})
        cls.related = Offer.objects.create(supplier=cls.supplier, brand='TEST', article='RELATED',
            name='Колодки', cost=Decimal('3000'), stock='2')
        cls.unverified = Offer.objects.create(supplier=cls.supplier, brand='TEST', article='UNVERIFIED',
            name='Рычаг', cost=Decimal('3000'), stock='4')
        cls.old = Offer.objects.create(supplier=cls.supplier, brand='TEST', article='OLD',
            name='Рычаг', cost=Decimal('3000'), stock='4', applicability='1985')
        for offer in (cls.direct, cls.related):
            VehicleFitment.objects.bulk_create([
                VehicleFitment(offer=offer, make='Make', model=f'Model {i}',
                    details=cls.year, verified=True) for i in range(30)
            ])
        VehicleFitment.objects.create(offer=cls.unverified, make='Make', model='Unchecked',
            details=cls.year, verified=False)
        Offer.objects.create(supplier=cls.supplier, brand='TEST', article='BELOW',
            name='Амортизатор', cost=Decimal('1999'), stock='2', applicability=cls.year)
        Offer.objects.create(supplier=cls.supplier, brand='TEST', article='ZERO',
            name='Амортизатор', cost=Decimal('2000'), stock='0', applicability=cls.year)

    def setUp(self):
        cache.clear()
        caches['procurement_options'].clear()
        self.client.force_login(self.user)
        self.url = reverse('procurement:selection_rules')

    def test_exists_preserves_reference_selection_without_join_or_distinct(self):
        rows = Offer.objects.filter(supplier=self.supplier)
        years = range(max(1980, date.today().year - 20), date.today().year + 2)
        pattern = '(' + '|'.join(map(str, years)) + ')'
        reference = rows.filter(Q(applicability__regex=pattern) |
            Q(fitments__verified=True, fitments__details__regex=pattern)).distinct()
        result = matching_years(rows, 20)
        self.assertCountEqual(result.values_list('pk', flat=True),
                              reference.values_list('pk', flat=True))
        sql = str(result.query).upper()
        self.assertIn('EXISTS', sql)
        self.assertNotIn('JOIN "PROCUREMENT_VEHICLEFITMENT"', sql)
        self.assertNotIn('DISTINCT', sql)

    def test_cold_page_counts_fitments_once_and_agrees_with_export(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['matched'], 2)
        self.assertEqual(response.context['before_years'], 4)
        self.assertEqual(response.context['year_unknown'], 2)
        self.assertEqual(filtered_offers(self.rule).count(), response.context['matched'])
        fitment_queries = [q['sql'] for q in queries if 'procurement_vehiclefitment' in q['sql']]
        self.assertEqual(len(fitment_queries), 1)

    def test_cached_page_does_not_repeat_catalogue_counts_for_download_links(self):
        first = self.client.get(self.url)
        cache.clear()  # A replacement HTTP process has an empty local cache.
        with CaptureQueriesContext(connection) as queries:
            second = self.client.get(self.url)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.context['matched'], first.context['matched'])
        self.assertEqual(second.context['json_batches'], first.context['json_batches'])
        self.assertFalse(any('procurement_vehiclefitment' in q['sql'] for q in queries))
        offer_counts = [q['sql'] for q in queries if 'COUNT(' in q['sql'].upper()
                        and 'FROM "procurement_offer"' in q['sql']]
        self.assertEqual(offer_counts, [])

    def test_changed_saved_criteria_does_not_reuse_previous_count(self):
        self.assertEqual(self.client.get(self.url).context['matched'], 2)
        self.rule.min_cost = Decimal('3000')
        self.rule.save(update_fields=['min_cost'])
        response = self.client.get(self.url)
        self.assertEqual(response.context['matched'], 1)
        self.assertEqual(filtered_offers(self.rule).count(), 1)

    def test_invalid_form_keeps_saved_export_count(self):
        response = self.client.post(self.url, {'action': 'save', 'supplier': self.supplier.pk,
            'min_cost': '-1', 'max_vehicle_age': '20', 'in_stock_only': 'on',
            'classification_mode': 'auto'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['matched'], filtered_offers(self.rule).count())

    def test_background_updates_completed_counts_without_blocking_navigation(self):
        from unittest.mock import patch
        from time import time
        from .selection_rule_views import warm_selection_preview
        first=self.client.get(self.url)
        self.assertEqual(first.context['matched'],2)
        Offer.objects.filter(pk=self.old.pk).update(applicability=self.year)
        # The last complete preview remains usable while the consumer refreshes.
        with patch('procurement.selection_rule_views.time',return_value=time()+61):
            warm_selection_preview()
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get(self.url)
        self.assertEqual(response.context['matched'],3)
        self.assertFalse(any('procurement_vehiclefitment' in q['sql'] for q in queries))
