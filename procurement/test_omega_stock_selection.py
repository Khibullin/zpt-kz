"""Regression tests for Omega's supplier-specific stock notation."""
from decimal import Decimal

from django.test import TestCase

from .models import Offer, Supplier
from .selection_rule_views import _stock_filter


class OmegaStockSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        supplier = Supplier.objects.create(name='Omega')
        for article, stock in (
            ('TWENTY', '20>'),
            ('TWO', '2'),
            ('ZERO', '0'),
            ('UNKNOWN', ''),
        ):
            Offer.objects.create(
                supplier=supplier, article=article, brand='TEST',
                name=article, stock=stock, cost=Decimal('3000'),
            )

    def test_twenty_plus_is_included_in_same_positive_stock_scope(self):
        selected = set(Offer.objects.filter(_stock_filter()).values_list('article', flat=True))
        self.assertEqual(selected, {'TWENTY', 'TWO'})
