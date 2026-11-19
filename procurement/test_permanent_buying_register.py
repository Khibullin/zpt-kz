"""Permanent buying-register regression tests."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from .models import Offer, Supplier, Preferences, PermanentBuyingItem
from .permanent_buying_visible import visible_permanent_buying_rows


class PermanentBuyingRegisterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='permanent_buying_tester', password='test-only')
        self.supplier = Supplier.objects.create(name='Omega')
        self.preferences = Preferences.objects.create(
            user=self.user, commission=Decimal('10'), expenses=Decimal('250'))
        self.offer = Offer.objects.create(
            supplier=self.supplier, brand='TEST', article='T-100',
            name='Радиатор', cost=Decimal('10000'), stock='7',
            kaspi_price=Decimal('22000'), match_verified=True)
        self.saved = PermanentBuyingItem.objects.create(
            offer=self.offer, source='historical', title=self.offer.name,
            kaspi_url='https://kaspi.kz/shop/p/example-123456/',
            cost_snapshot=Decimal('10000'), kaspi_price_snapshot=Decimal('22000'),
            commission_snapshot=Decimal('10'), expenses_snapshot=Decimal('250'),
            profit_snapshot=Decimal('9550'), roi_snapshot=Decimal('95.5000'))
        self.factory = RequestFactory()

    def test_old_item_remains_after_source_price_and_stock_change(self):
        self.offer.cost = Decimal('30000')
        self.offer.stock = '0'
        self.offer.kaspi_price = Decimal('12000')
        self.offer.match_verified = False
        self.offer.save()
        request = self.factory.get('/office/procurement/', {'mode': 'best'})
        request.user = self.user
        result = visible_permanent_buying_rows(
            request, self.preferences, {'supplier': self.supplier})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['profit'], Decimal('9550'))
        self.assertEqual(result[0]['display_cost'], Decimal('10000'))
        self.saved.refresh_from_db()
        self.assertEqual(self.saved.cost_snapshot, Decimal('10000'))

    def test_rank_by_saved_net_profit_not_roi(self):
        other = Offer.objects.create(
            supplier=self.supplier, brand='TEST', article='T-200',
            name='Фильтр', cost=Decimal('3000'), stock='2')
        PermanentBuyingItem.objects.create(
            offer=other, source='apify', title=other.name,
            cost_snapshot=Decimal('3000'), kaspi_price_snapshot=Decimal('8000'),
            commission_snapshot=Decimal('10'), expenses_snapshot=Decimal('250'),
            profit_snapshot=Decimal('3950'), roi_snapshot=Decimal('131.6667'),
            identity_status='candidate')
        request = self.factory.get('/office/procurement/', {'mode': 'best'})
        request.user = self.user
        result = visible_permanent_buying_rows(
            request, self.preferences, {'supplier': self.supplier})
        self.assertEqual([r['offer'].pk for r in result],
                         [self.offer.pk, other.pk])
