from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import Supplier, Offer, Preferences, KaspiSellerSnapshot
from .views import filtered_rows

class CatalogFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('catalog-filter')
        self.prefs = Preferences.objects.create(user=self.user, commission=10, expenses=250)
        self.supplier = Supplier.objects.create(name='Catalog supplier')
        self.pending = Offer.objects.create(supplier=self.supplier, name='Pending', article='P', cost=100)
        self.empty = Offer.objects.create(supplier=self.supplier, name='Empty', article='E', cost=100, match_verified=True, kaspi_url='https://kaspi.kz/shop/p/empty-123/')
        KaspiSellerSnapshot.objects.create(offer=self.empty, source_url=self.empty.kaspi_url, checked_on=timezone.localdate(), complete=True, sellers=[])
    def rows(self, **filters):
        request = RequestFactory().get('/', {'mode': filters.pop('mode', 'roi')})
        request.user = self.user
        return filtered_rows(request, self.prefs, filters)
    def test_complete_catalog_and_multistatus(self):
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(self.rows(kaspi_status=['pending', 'empty'])), 2)
        self.assertEqual([r['offer'].pk for r in self.rows(kaspi_status=['empty'])], [self.empty.pk])
        self.assertEqual(len(self.rows(mode='reviewed')), 2)
    def test_incomplete_or_stale_empty_is_pending(self):
        self.empty.kaspi_sellers.complete = False
        self.empty.kaspi_sellers.save()
        self.assertEqual(len(self.rows(kaspi_status=['empty'])), 0)
        self.assertEqual(len(self.rows(kaspi_status=['pending'])), 2)
    def test_best_requires_sales_identity_and_stock(self):
        self.empty.kaspi_price = 5000
        self.empty.stock = '5'
        self.empty.supplier_checked_on = self.empty.kaspi_price_checked_on = self.empty.checked_on = self.empty.evidence_date = timezone.localdate()
        self.empty.sales_30 = 2
        self.empty.save()
        snapshot = self.empty.kaspi_sellers
        snapshot.sellers = [{'name': 'Shop', 'price': '5000'}]
        snapshot.save()
        rows = self.rows(mode='best')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['markup'], rows[0]['profit'] / self.empty.cost * 100)
        self.empty.sales_30 = None
        self.empty.recent_reviews = 3
        self.empty.save()
        self.assertEqual(self.rows(mode='best'), [])
