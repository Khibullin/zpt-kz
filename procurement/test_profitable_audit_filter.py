"""Regression: a previously profitable SKU is NOT profitable after a new Apify price audit."""
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import (Offer, Supplier, Preferences, PermanentBuyingItem, KaspiPriceAudit)
from .permanent_buying_visible import visible_permanent_buying_rows, apify_source_summary


class ProfitableAuditFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('audit_policy', password='test-only')
        self.supplier = Supplier.objects.create(name='Omega')
        self.preferences = Preferences.objects.create(
            user=self.user, commission=Decimal('12.5'), expenses=Decimal('250'))
        self.source = 'dataset_kaspi-api_2026-11-21.json'

    def sku(self, article, name='Рычаг', category='Подвеска', cost='10000',
            price='26000', audit_roi='75', audit_profit='7500'):
        offer = Offer.objects.create(
            supplier=self.supplier, brand='TEST', article=article, name=name,
            category=category, cost=Decimal(cost), stock='4')
        historical = PermanentBuyingItem.objects.create(
            offer=offer, source='apify', title=name, cost_snapshot=Decimal(cost),
            kaspi_price_snapshot=Decimal(price), commission_snapshot=Decimal('12.5'),
            expenses_snapshot=Decimal('250'), profit_snapshot=Decimal('12000'),
            roi_snapshot=Decimal('120'), identity_status='candidate')
        audit = KaspiPriceAudit.objects.create(
            offer=offer, source_filename=self.source, min_price=Decimal(price),
            cost_snapshot=Decimal(cost), commission_snapshot=Decimal('12.5'),
            expenses_snapshot=Decimal('250'), profit=Decimal(audit_profit),
            roi=Decimal(audit_roi), identity_status='needs_review')
        return offer, historical, audit

    def result(self, source=None):
        req = SimpleNamespace(user=self.user, GET={'apify_source': source} if source else {})
        return visible_permanent_buying_rows(req, self.preferences,
                                            {'supplier': self.supplier})

    def test_latest_price_filters_below_50_and_preserves_archive(self):
        good, _, _ = self.sku('GOOD')
        bad, old, audit = self.sku('BAD', audit_roi='30', audit_profit='3000')
        shown = {row['offer'].pk for row in self.result(self.source)}
        self.assertIn(good.pk, shown)
        self.assertNotIn(bad.pk, shown)
        old.refresh_from_db()
        audit.refresh_from_db()
        self.assertEqual(old.roi_snapshot, Decimal('120'))
        self.assertEqual(audit.roi, Decimal('30'))
        self.assertEqual(apify_source_summary(self.source, self.supplier)['excluded'], 1)

    def test_consumable_cost_threshold_and_springs(self):
        low, _, _ = self.sku('LOW', cost='5000', audit_roi='75')
        consumable, _, _ = self.sku(
            'FILTER', name='Масляный фильтр', category='Расходники',
            cost='2000', audit_roi='75')
        spring, _, _ = self.sku(
            'SPRING', name='Пружина подвески', cost='10000', audit_roi='75')
        shown = {row['offer'].pk for row in self.result(self.source)}
        self.assertNotIn(low.pk, shown)
        self.assertIn(consumable.pk, shown)
        self.assertNotIn(spring.pk, shown)

    def test_profitability_percentage_is_primary_order_not_absolute_profit(self):
        high_roi, _, _ = self.sku('HIGH_ROI', audit_roi='240.75', audit_profit='1000')
        high_cash, _, _ = self.sku('HIGH_CASH', audit_roi='62.06', audit_profit='23000')
        tie_cash, _, _ = self.sku('TIE_CASH', audit_roi='62.06', audit_profit='24000')
        shown = [row['offer'].pk for row in self.result(self.source)]
        self.assertEqual(shown, [high_roi.pk, tie_cash.pk, high_cash.pk])

    def test_selected_export_cannot_fall_back_to_other_export_price(self):
        offer, _, _ = self.sku('RECHECK', audit_roi='30', audit_profit='3000')
        KaspiPriceAudit.objects.create(
            offer=offer, source_filename='other-export.json',
            min_price=Decimal('40000'), cost_snapshot=Decimal('10000'),
            commission_snapshot=Decimal('12.5'), expenses_snapshot=Decimal('250'),
            profit=Decimal('24750'), roi=Decimal('247.5'))
        self.assertNotIn(offer.pk, {r['offer'].pk for r in self.result(self.source)})
        self.assertIn(offer.pk, {r['offer'].pk for r in self.result('other-export.json')})
