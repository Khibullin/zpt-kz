from datetime import date
from io import BytesIO
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from openpyxl import load_workbook
from .models import Item, ImportBatch, StockSnapshot, ItemAlias, StockDocument, StockEntry
from .stock_reporting import stock_rows, stock_summary


class WarehouseReportingTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='stock-report',password='test')
        self.client.force_login(self.user)
        self.item=Item.objects.create(code='AG-A123',article='A123',name='Свечи A123',unit='set')
        self.batches={k:ImportBatch.objects.create(kind=k,digest=k,filename=k+'.xlsx',as_of=date(2026,9,21),created_by=self.user) for k in ('pp1','stock')}
        self.snapshot('pp1','A123',10)
        self.snapshot('stock','sku1',4)

    def snapshot(self,kind,sku,quantity):
        return StockSnapshot.objects.create(batch=self.batches[kind],warehouse='PP1' if kind=='pp1' else 'PP2',sku=sku,name='Свечи A123',quantity=quantity)

    def test_snapshot_fallback_no_double_count_or_posting(self):
        row=stock_rows()[0]
        self.assertEqual((row.pp1,row.pp2,row.kaspi,row.total),(10,4,4,14))
        self.assertIn('предварительное',row.kaspi_source)
        self.assertEqual(stock_summary([row])[-1]['groups'],[{'unit':'Комплект','quantity':14}])
        self.assertFalse(StockEntry.objects.exists())

    def test_overlapping_skus_unknown_not_sum(self):
        self.snapshot('stock','sku2',4)
        row=stock_rows()[0]
        self.assertIsNone(row.kaspi)
        self.assertIsNone(row.total)
        self.assertIn('Несколько SKU',row.kaspi_source)

    def test_ledger_precedes_snapshot_and_zero_is_known(self):
        doc=StockDocument.objects.create(item=self.item,kind='opening',warehouse='PP2',quantity=1,date=date.today(),created_by=self.user)
        StockEntry.objects.create(document=doc,warehouse='PP2',quantity=0,created_by=self.user)
        row=stock_rows()[0]
        self.assertEqual((row.pp2,row.kaspi,row.total,row.difference),(0,4,10,-4))

    def test_confirmed_alias_owned_elsewhere_not_reused(self):
        other=Item.objects.create(code='OTHER',name='Другой')
        ItemAlias.objects.create(item=other,source='kaspi',code='sku1',confirmed_by=self.user)
        self.assertIsNone(stock_rows([self.item])[0].kaspi)

    def test_filters_pagination_export_use_full_selection(self):
        for n in range(51):
            Item.objects.create(code=f'Z{n}',name='Без остатка',unit='pending')
        response=self.client.get(reverse('inventory:stock'))
        self.assertEqual(response.context['item_count'],52)
        self.assertEqual(response.context['summary'][0]['unknown'],51)
        self.assertContains(response,'Kaspi')
        filtered=self.client.get(reverse('inventory:items'),{'warehouse':'Kaspi','stock_state':'positive'})
        self.assertEqual(filtered.context['item_count'],1)
        response=self.client.get(reverse('inventory:items'),{'export':'xlsx'})
        rows=list(load_workbook(BytesIO(response.content)).active.values)
        header=next(n for n,row in enumerate(rows) if row[0]=='Артикул')
        self.assertEqual(rows[header+1][5:10],(10,'Файл от 21.09.2026',4,'По Kaspi · Файл от 21.09.2026 · сопоставление предварительное',14))
        self.assertEqual(len(rows)-header-1,52)
        self.assertIn(('Наименований',52),[tuple(v for v in r if v is not None) for r in rows])
