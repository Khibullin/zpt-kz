from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from .bundles import post_bundle
from .models import GoodsDocument, Item, StockDocument


class BalancePriceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='prices-admin', password='test')
        self.client.force_login(self.user)
        self.item = Item.objects.create(code='Z', article='Z', name='Деталь', unit='piece')
        self.url = reverse('inventory:items')

    def post(self, item, quantity, cost=None, warehouse='PP2', kind='receipt'):
        doc = GoodsDocument.objects.create(kind=kind, warehouse=warehouse,
            date=date.today(), accepted_on=date.today(), counterparty='Omega',
            number=str(GoodsDocument.objects.count()), created_by=self.user)
        StockDocument.objects.create(bundle=doc, kind=kind, item=item,
            warehouse=warehouse, quantity=quantity, unit_cost=cost,
            date=doc.date, external_number=doc.number, counterparty='Omega', created_by=self.user)
        post_bundle(doc.pk, self.user)

    def test_price_uses_remaining_fifo_and_selected_warehouse(self):
        self.post(self.item, 3, Decimal('100'))
        self.post(self.item, 1, Decimal('300'))
        self.post(self.item, 1, kind='sale')
        self.post(self.item, 2, Decimal('400'), warehouse='PP1')
        for warehouse, value, price in [('PP2', Decimal('500'), Decimal('500') / 3),
                                         ('PP1', Decimal('800'), Decimal('400')),
                                         ('', Decimal('1300'), Decimal('260'))]:
            response = self.client.get(self.url, {'warehouse': warehouse})
            row = response.context['page'][0]
            self.assertEqual(row.stock_value, value)
            self.assertEqual(row.purchase_price, price)
            self.assertContains(response, 'Закупочная цена, ₸/ед.')
            self.assertContains(response, 'Стоимость остатка, ₸')

    def test_unknown_prices_last_before_pagination_and_same_export_order(self):
        Item.objects.bulk_create([Item(code=f'A{n:02}', name='Неизвестная цена') for n in range(51)])
        other = Item.objects.create(code='B', name='Другая деталь', unit='piece')
        self.post(self.item, 1, Decimal('300'))
        self.post(other, 4, Decimal('100'))
        for sort, expected in [('price', other.pk), ('-price', self.item.pk),
                               ('value', self.item.pk), ('-value', other.pk)]:
            response = self.client.get(self.url, {'sort': sort})
            self.assertEqual(response.context['page'][0].pk, expected)
            self.assertEqual(response.context['page'].paginator.count, 53)
            self.assertIsNone(response.context['page'][2].purchase_price)
        response = self.client.get(self.url, {'sort': '-price', 'export': 'xlsx', 'page': 2})
        book = load_workbook(BytesIO(response.content))
        sheet = book.active
        headers = [c.value for c in sheet[1]]
        col = headers.index('Закупочная цена, ₸/ед.') + 1
        self.assertEqual(sheet.cell(2, 1).value, 'Z')
        self.assertEqual(sheet.cell(2, col).value, 300)
        self.assertEqual(sheet.cell(3, col).value, 100)
        self.assertEqual(sheet.cell(4, col).value, 'Нет данных')
        self.assertEqual(sheet.max_row, 54)
        self.assertIn(('Сортировка', 'Закупочная цена по убыванию'),
                      [tuple(row[:2]) for row in book['Сводка и фильтры'].values])

    def test_zero_balance_has_zero_value_but_no_purchase_price(self):
        self.post(self.item, 1, Decimal('100'))
        self.post(self.item, 1, kind='sale')
        response = self.client.get(self.url, {'warehouse': 'PP2'})
        row = response.context['page'][0]
        self.assertEqual(row.stock_value, 0)
        self.assertIsNone(row.purchase_price)
        self.assertContains(response, 'Нет данных')

    def test_snapshot_retail_price_is_never_used_as_purchase_price(self):
        from .models import ImportBatch, StockSnapshot
        batch = ImportBatch.objects.create(kind='pp1', digest='prices-snapshot',
            filename='prices.xlsx', as_of=date.today(), created_by=self.user)
        StockSnapshot.objects.create(batch=batch, warehouse='PP1', sku='Z',
            name='Деталь', quantity=4, price=9999)
        response = self.client.get(self.url, {'warehouse': 'PP1', 'basis': 'snapshot'})
        row = response.context['page'][0]
        self.assertEqual(row.filtered_stock, 4)
        self.assertIsNone(row.purchase_price)
        self.assertIsNone(row.stock_value)
