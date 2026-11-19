"""Regression tests for the approved consumables/other parts buying policy."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .catalog_paging import (
    buying_cost_query, cumulative_profitable_query, is_consumable,
    net_buying_profit, profitable_product_exclusions,
)
from .models import Offer, Preferences, Supplier


class BuyingCategoryPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.supplier = Supplier.objects.create(name='Policy supplier')
        cls.user = get_user_model().objects.create_user('policy-2026-11-19')
        cls.prefs = Preferences.objects.create(
            user=cls.user, commission=Decimal('10'), expenses=Decimal('250')
        )

    def item(self, name, cost, kaspi, category='Запчасти'):
        return Offer.objects.create(
            supplier=self.supplier, brand='TEST', article=name[:30],
            name=name, category=category, cost=Decimal(cost),
            kaspi_price=Decimal(kaspi), stock='20',
        )

    def test_wholesale_threshold_and_exclusion(self):
        valid_consumable = self.item('Фильтр воздушный', '2000', '4000')
        self.item('Фильтр салонный', '1999', '4000')
        valid_part = self.item('Опора двигателя', '5001', '10000')
        self.item('Опора КПП', '5000', '10000')
        self.item('Пружина подвески', '7000', '20000')
        self.item('Coil spring', '7000', '20000')
        self.item('Крышка маслозаливная', '3000', '10000')
        self.item('Фильтр топливный', '3000', '3600')  # Net ROI below 50%.
        result = cumulative_profitable_query(Offer.objects.all(), self.prefs)
        self.assertEqual(set(result.values_list('pk', flat=True)),
                         {valid_consumable.pk, valid_part.pk})

    def test_net_profit_deducts_packaging_once(self):
        self.assertEqual(
            net_buying_profit(Decimal('4000'), Decimal('2000'),
                              Decimal('10'), Decimal('250')),
            Decimal('1350'),
        )
        self.assertFalse(is_consumable('Крышка маслозаливная', 'Двигатель'))
        self.assertTrue(is_consumable('Фильтр салонный', 'Автозапчасти'))
