"""The comparison upload is isolated from operational purchasing."""
from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from openpyxl import Workbook

from procurement.models import Offer, Supplier, SupplierComparisonPriceList
from procurement.supplier_price_comparison import _snapshot


def price_file(lines):
    book = Workbook()
    sheet = book.active
    sheet.append(['Бренд', 'Артикул', 'Наименование', 'Цена', 'Остаток'])
    for row in lines:
        sheet.append(row)
    buffer = BytesIO()
    book.save(buffer)
    return SimpleUploadedFile('supplier.xlsx', buffer.getvalue(),
                              content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


class SupplierPriceComparisonTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='comparison_admin', email='comparison@example.com', password='test-pass')
        self.client.force_login(self.user)
        self.url = reverse('procurement:supplier_price_comparison')
        self.omega = Supplier.objects.create(name='Omega')
        Offer.objects.create(supplier=self.omega, brand='WINKOD', article='WS9991',
                             name='Test', cost=Decimal('1800'), stock='2')

    def upload(self, code, rows):
        return self.client.post(self.url, {
            'action': 'upload', 'supplier': code,
            'checked_on': date.today().isoformat(),
            'file': price_file(rows)}, follow=True)

    def test_upload_replaces_only_comparison_snapshot(self):
        response = self.upload('Phaeton', [['WINKOD', 'WS9991', 'Part', 2300, 8]])
        self.assertEqual(response.status_code, 200)
        batch = SupplierComparisonPriceList.objects.get(supplier_code='Phaeton')
        self.assertEqual(batch.available_count, 1)
        self.assertEqual(Offer.objects.get(supplier=self.omega).cost, Decimal('1800'))
        self.assertEqual(_snapshot()['wins']['Omega'], 1)

    def test_invalid_upload_preserves_previous_snapshot(self):
        self.upload('ZakazAuto', [['WINKOD', 'WS9991', 'Part', 1300, 8]])
        previous = SupplierComparisonPriceList.objects.get(supplier_code='ZakazAuto')
        self.upload('ZakazAuto', [['', '', '', 0, '']])
        previous.refresh_from_db()
        self.assertEqual(previous.available_count, 1)
        self.assertEqual(previous.rows[0]['cost'], '1300')

    def test_cheapest_and_equal_prices(self):
        self.upload('Phaeton', [['WINKOD', 'WS9991', 'Part', 2000, 5]])
        self.upload('ZakazAuto', [['WINKOD', 'WS9991', 'Part', 2000, 4]])
        groups = _snapshot()
        self.assertEqual(groups['shared'], 1)
        self.assertEqual(groups['wins']['Omega'], 1)
        self.assertEqual(groups['rows'][0]['saving'], Decimal('200'))
        self.assertEqual(self.client.post(self.url, {'action': 'recalculate'}).status_code, 302)

    def test_omega_only_prices_remain_visible_without_false_matches(self):
        self.upload('Phaeton', [['OTHER', 'X200', 'Other part', 2300, 5]])
        comparison = _snapshot()
        self.assertEqual(comparison['overlaps']['Phaeton_Omega'], 0)
        self.assertEqual(comparison['omega_only'], 1)
        self.assertEqual(comparison['counts']['Omega'], 1)
        response = self.client.get(self.url, {'present': 'Omega', 'shared': '0', 'min_cost': '1800'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'WS9991')
        self.assertNotContains(response, 'X200')

    def test_exact_omega_match_reports_pairwise_overlap(self):
        self.upload('Phaeton', [['WINKOD', 'WS-9991', 'Same part', 2300, 5]])
        comparison = _snapshot()
        self.assertEqual(comparison['overlaps']['Phaeton_Omega'], 1)
        self.assertEqual(comparison['wins']['Omega'], 1)

    def test_default_minimum_price_is_inclusive_and_configurable(self):
        self.upload('Phaeton', [
            ['LOW', 'L01', 'Cheap part', 1999, 5],
            ['EDGE', 'E01', 'Boundary part', 2000, 4],
            ['HIGH', 'H01', 'Expensive part', 2500, 3],
        ])
        response = self.client.get(self.url, {'shared': '0'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'L01')
        self.assertContains(response, 'E01')
        self.assertContains(response, 'H01')
        response = self.client.get(self.url, {'min_cost': '1500', 'shared': '0'})
        self.assertContains(response, 'L01')
        response = self.client.get(self.url, {'min_cost': '2500', 'shared': '0'})
        self.assertNotContains(response, 'E01')
        self.assertContains(response, 'H01')

    def test_price_filter_keeps_real_cheapest_supplier(self):
        self.upload('Phaeton', [['WINKOD', 'WS9991', 'Part', 2000, 5]])
        response = self.client.get(self.url)  # Omega is cheaper at 1800
        self.assertNotContains(response, 'WS9991')
        response = self.client.get(self.url, {'min_cost': '1800'})
        self.assertContains(response, 'WS9991')
        self.assertEqual(_snapshot(Decimal('1800'))['wins']['Omega'], 1)

    def test_numbered_navigation_preserves_filters(self):
        from unittest.mock import patch
        from django.utils.http import urlencode
        from decimal import Decimal
        rows = [
            {'brand': 'TEST', 'article': f'{i:04}', 'name': 'Example',
             'prices': {'Phaeton': Decimal('2000')}, 'stock': {'Phaeton': '1'},
             'winner': 'Phaeton', 'alternatives': 1, 'saving': None, 'percent': None}
            for i in range(151)
        ]
        snapshot = {'rows': rows, 'counts': {'Phaeton': 151, 'ZakazAuto': 0, 'Omega': 0},
                    'wins': {'Phaeton': 0, 'ZakazAuto': 0, 'Omega': 0}, 'shared': 0,
                    'overlaps': {'Phaeton_Omega': 0, 'ZakazAuto_Omega': 0},
                    'brand_overlaps': {'Phaeton_Omega': 0, 'ZakazAuto_Omega': 0},
                    'article_overlaps': {'Phaeton_Omega': 0, 'ZakazAuto_Omega': 0},
                    'omega_only': 0}
        with patch('procurement.supplier_price_comparison._snapshot', return_value=snapshot):
            response = self.client.get(self.url, {'page': '2', 'min_cost': '2000', 'q': 'TEST', 'shared': '0'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Страница 2 из 4', count=2)
        self.assertContains(response, 'page=3#price-results')
        self.assertContains(response, 'min_cost=2000')
        self.assertContains(response, 'name="page"')


class ComparisonVersionCacheTests(TransactionTestCase):
    def test_uploaded_versions_are_reused_and_new_upload_is_visible_immediately(self):
        from tempfile import TemporaryDirectory
        from django.core.cache import caches
        from django.test import override_settings
        user=get_user_model().objects.create_user('versioned-comparison')
        for code,cost in [('Omega','2000'),('Phaeton','2300'),('ZakazAuto','2500')]:
            SupplierComparisonPriceList.objects.create(supplier_code=code,filename=code+'.xlsx',
                checked_on=date.today(),uploaded_by=user,row_count=1,available_count=1,
                rows=[{'brand':'EEP','article':'A1','name':'Part','cost':cost,'stock':'1'}])
        with TemporaryDirectory() as root, override_settings(CACHES={
            'default':{'BACKEND':'django.core.cache.backends.locmem.LocMemCache'},
            'supplier_comparison':{'BACKEND':'django.core.cache.backends.filebased.FileBasedCache',
                                   'LOCATION':root+'/private','OPTIONS':{'MAX_ENTRIES':8}}}):
            first=_snapshot(Decimal('2000'))
            self.assertEqual(first['rows'][0]['winner'],'Omega')
            with patch('procurement.supplier_price_comparison.iter_json_rows',side_effect=AssertionError('must reuse uploaded version')):
                self.assertEqual(_snapshot(Decimal('2000')),first)
            # Idle warming must not deserialize the whole cache on every pass.
            with patch.object(caches['supplier_comparison'],'get',side_effect=AssertionError('must only inspect expiry')):
                self.assertIsNone(_snapshot(Decimal('2000'),warm_only=True))
            batch=SupplierComparisonPriceList.objects.get(supplier_code='Omega')
            batch.rows[0]['cost']='2600'
            batch.save()
            fresh=_snapshot(Decimal('2000'))
            self.assertEqual(fresh['rows'][0]['winner'],'Phaeton')
            self.assertEqual(fresh['rows'][0]['prices']['Omega'],Decimal('2600'))
            self.assertEqual(_snapshot(Decimal('3000'))['rows'],[])
            batch.refresh_from_db()
            self.assertEqual(batch.rows[0]['cost'],'2600')
