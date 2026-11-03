from datetime import date
from decimal import Decimal
from io import BytesIO
from openpyxl import Workbook
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .importing import import_file
from .models import StockSnapshot

class AccountingTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='accounting',password='test-password')
    def stock_file(self):
        w=Workbook();s=w.active
        s.append(['SKU','model','brand','price','PP1','PP2'])
        s.append(['001','Фильтр','AG Parts',2200,'no','3'])
        b=BytesIO();w.save(b);return b.getvalue()
    def test_idempotent_snapshot_and_access(self):
        raw=self.stock_file()
        _,count=import_file(raw,'stock','test.xlsx',date.today(),self.user)
        self.assertEqual(count,1)
        _,count=import_file(raw,'stock','test.xlsx',date.today(),self.user)
        self.assertEqual(count,0)
        self.assertEqual(StockSnapshot.objects.get().sku,'001')
        self.assertEqual(self.client.get(reverse('inventory:overview')).status_code,302)
        self.client.force_login(self.user)
        for route in ['overview','stock','sales','finance','upload']:
            r=self.client.get(reverse('inventory:'+route))
            self.assertEqual(r.status_code,200)
        r=self.client.get(reverse('inventory:stock'),{'q':'missing'})
        self.assertNotContains(r,'Фильтр</td>')
    def test_regular_user_denied(self):
        u=get_user_model().objects.create_user(username='regular',password='test-password')
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('inventory:overview')).status_code,403)
        self.assertEqual(self.client.post(reverse('inventory:upload')).status_code,403)
    def test_invalid_quantity_atomic(self):
        raw=self.stock_file()
        import openpyxl
        w=openpyxl.load_workbook(BytesIO(raw));w.active['F2']=-1
        b=BytesIO();w.save(b)
        with self.assertRaises(ValueError): import_file(b.getvalue(),'stock','bad.xlsx',date.today(),self.user)
        self.assertFalse(StockSnapshot.objects.exists())
    def test_sales_corrections_and_overlapping_reports(self):
        from .models import KaspiOperation
        headers=['#','Номер заказа (ID/RRN)','Дата операции','Тип операции','Детали покупки','Сумма операции (т)','Комиссия за операции (т)','Комиссия Kaspi Pay (т)','Стоимость услуги за Kaspi Доставку','Оплата услуг за акцию Бонусы на товар','Оплата услуг за акцию Бонусы за отзыв','Комиссия за операции по карте (т)','Комиссия за обеспечение платежа (т)','Комиссия Kaspi Travel (т)']
        def report(extra=False):
            w=Workbook();s=w.active;s.append(headers)
            s.append([1,'order1','31.10.2026','Покупка','Фильтр',1000,-115.42,-9.5,-173,None,-500,None,None,None])
            s.append([2,'order1','31.10.2026','Покупка','Фильтр',None,None,None,None,None,500,None,None,None])
            if extra:s.append([3,'order2','31.10.2026','Покупка','Другой фильтр',2000,None,None,None,None,None,None,None,None])
            b=BytesIO();w.save(b);return b.getvalue()
        _,count=import_file(report(),'sales','report.xlsx',date.today(),self.user)
        self.assertEqual(count,2)
        self.assertEqual(sum(o.after_fees for o in KaspiOperation.objects.all()),Decimal('702.08'))
        _,count=import_file(report(True),'sales','overlap.xlsx',date.today(),self.user)
        self.assertEqual(count,1)
        self.assertEqual(KaspiOperation.objects.count(),3)
        self.client.force_login(self.user)
        response=self.client.get(reverse('inventory:finance'),{'q':'order1'})
        self.assertContains(response,'702,08')
    def test_pp1_unknown_quantity_preserved(self):
        w=Workbook();s=w.active;s.append(['№','Артикул','PP1 после, шт.'])
        s.append([1,'known',7]);s.append([2,'unknown',None])
        b=BytesIO();w.save(b)
        import_file(b.getvalue(),'pp1','pp1.xlsx',date(2026,9,21),self.user)
        self.assertIsNone(StockSnapshot.objects.get(sku='unknown').quantity)
        self.client.force_login(self.user)
        r=self.client.get(reverse('inventory:stock'),{'warehouse':'PP1','state':'unknown'})
        self.assertContains(r,'unknown')
        self.assertNotContains(r,'>known</td>')
