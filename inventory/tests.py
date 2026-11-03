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

class StockDocumentsTests(TestCase):
    def setUp(self):
        from .models import Item
        self.user=get_user_model().objects.create_superuser(username='stock-admin',password='test-password')
        self.item=Item.objects.create(code='FILTER',name='Фильтр')
        self.client.force_login(self.user)
    def document(self,**kwargs):
        from .models import StockDocument
        data=dict(item=self.item,warehouse='PP1',quantity=10,date=date.today(),created_by=self.user,kind='opening')
        data.update(kwargs)
        return StockDocument.objects.create(**data)
    def test_transfer_partial_receipt_and_conservation(self):
        from .services import post_document,receive_transfer,balance
        from .models import StockEvent
        from django.core.exceptions import ValidationError
        opening=self.document();post_document(opening.pk,self.user)
        transfer=self.document(kind='transfer',destination='PP2',quantity=7)
        post_document(transfer.pk,self.user)
        self.assertEqual([balance(self.item,w) for w in ['PP1','PP2','TRANSIT']],[3,0,7])
        receive_transfer(transfer.pk,3,self.user)
        self.assertEqual([balance(self.item,w) for w in ['PP1','PP2','TRANSIT']],[3,3,4])
        with self.assertRaises(ValidationError):receive_transfer(transfer.pk,5,self.user)
        receive_transfer(transfer.pk,4,self.user)
        self.assertEqual([balance(self.item,w) for w in ['PP1','PP2','TRANSIT']],[3,7,0])
        with self.assertRaises(ValidationError):post_document(transfer.pk,self.user)
        with self.assertRaises(ValidationError):receive_transfer(transfer.pk,1,self.user)
        self.assertEqual(StockEvent.objects.filter(document=transfer).count(),3)
    def test_insufficient_stock_and_duplicate_opening(self):
        from .services import post_document,balance
        from .models import StockEntry
        from django.core.exceptions import ValidationError
        transfer=self.document(kind='transfer',destination='PP2')
        with self.assertRaises(ValidationError):post_document(transfer.pk,self.user)
        self.assertFalse(StockEntry.objects.exists())
        post_document(self.document().pk,self.user)
        with self.assertRaises(ValidationError):post_document(self.document().pk,self.user)
        self.assertEqual(balance(self.item,'PP1'),10)
    def test_receipt_requires_cost_and_supplier(self):
        from .services import post_document,balance
        from django.core.exceptions import ValidationError
        doc=self.document(kind='receipt')
        with self.assertRaises(ValidationError):post_document(doc.pk,self.user)
        doc.unit_cost=Decimal('1200');doc.counterparty='Поставщик';doc.save()
        post_document(doc.pk,self.user)
        self.assertEqual(balance(self.item,'PP1'),10)
    def test_pages_and_cash_validation(self):
        from .models import MoneyAccount
        from .forms import CashForm
        account=MoneyAccount.objects.create(name='Банк',opening_balance=10000,opening_date=date(2026,11,1),created_by=self.user)
        data={'account':account.pk,'date':'2026-10-31','kind':'supplier','amount':'2000','reference':'PAY1'}
        self.assertFalse(CashForm(data).is_valid())
        data['date']='2026-11-03'
        self.assertTrue(CashForm(data).is_valid())
        response=self.client.post(reverse('inventory:create',args=['cash']),data)
        self.assertEqual(response.status_code,302)
        duplicate=CashForm(data)
        self.assertFalse(duplicate.is_valid())
        for route in ['items','documents','cash']:
            self.assertEqual(self.client.get(reverse('inventory:'+route)).status_code,200)
        for kind in ['item','alias','document','account','cash']:
            self.assertEqual(self.client.get(reverse('inventory:create',args=[kind])).status_code,200)
        self.assertEqual(self.client.get(reverse('inventory:cash')).context['accounts'][0].current_balance,Decimal('8000'))
    def test_actions_post_only_and_access(self):
        doc=self.document()
        self.assertEqual(self.client.get(reverse('inventory:document_action',args=[doc.pk])).status_code,405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('inventory:document_action',args=[doc.pk]),{'action':'post'}).status_code,302)
        doc.refresh_from_db();self.assertEqual(doc.status,'draft')

class CostingTests(TestCase):
    def setUp(self):
        from .models import Item
        self.user=get_user_model().objects.create_superuser(username='fifo',password='test-password')
        self.item=Item.objects.create(code='FIFO',name='Фильтр')
    def post(self,kind,quantity,**kwargs):
        from .models import StockDocument
        from .services import post_document
        data=dict(kind=kind,item=self.item,warehouse='PP2',quantity=quantity,date=date.today(),created_by=self.user)
        data.update(kwargs)
        doc=StockDocument.objects.create(**data)
        return post_document(doc.pk,self.user)
    def test_fifo_across_batches_and_partial_returns(self):
        from .services import balance,post_document
        from django.core.exceptions import ValidationError
        self.post('receipt',2,unit_cost=100,counterparty='A')
        self.post('receipt',3,unit_cost=200,counterparty='B')
        sale=self.post('sale',4,external_number='ORDER1')
        self.assertEqual(sale.total_cost,Decimal('600'))
        ret=self.post('return',1,related_sale=sale,return_condition='good')
        self.assertEqual(ret.total_cost,Decimal('100'))
        ret=self.post('return',2,related_sale=sale,return_condition='damaged')
        self.assertEqual(ret.total_cost,Decimal('300'))
        self.assertEqual(balance(self.item,'PP2'),2)
        self.assertEqual(balance(self.item,'DAMAGED'),2)
        self.post('return',1,related_sale=sale,return_condition='good')
        with self.assertRaises(ValidationError):self.post('return',1,related_sale=sale,return_condition='good')
    def test_transfer_preserves_batch_cost(self):
        from .services import receive_transfer,balance
        self.post('receipt',4,warehouse='PP1',unit_cost=700,counterparty='A')
        transfer=self.post('transfer',3,warehouse='PP1',destination='PP2')
        receive_transfer(transfer.pk,2,self.user)
        sale=self.post('sale',2,external_number='TRANSFER-SALE')
        self.assertEqual(sale.total_cost,Decimal('1400'))
        self.assertEqual(balance(self.item,'TRANSIT'),1)
    def test_unknown_cost_not_zero_and_no_overselling(self):
        from .services import balance
        from django.core.exceptions import ValidationError
        self.post('opening',2)
        sale=self.post('sale',1,external_number='UNKNOWN')
        self.assertIsNone(sale.total_cost)
        with self.assertRaises(ValidationError):self.post('sale',2,external_number='TOO-MANY')
        self.assertEqual(balance(self.item,'PP2'),1)
    def test_pp1_cannot_sell_and_return_must_match(self):
        from .models import Item
        from django.core.exceptions import ValidationError
        self.post('opening',2,warehouse='PP1')
        with self.assertRaises(ValidationError):self.post('sale',1,warehouse='PP1',external_number='BAD')
        self.post('receipt',2,unit_cost=20,counterparty='A')
        sale=self.post('sale',1,external_number='GOOD')
        other=Item.objects.create(code='OTHER',name='Другая деталь')
        with self.assertRaises(ValidationError):self.post('return',1,item=other,related_sale=sale,return_condition='good')
    def test_cost_report_marks_missing_cost(self):
        self.post('opening',2)
        self.post('sale',1,external_number='REPORT')
        self.client.force_login(self.user)
        response=self.client.get(reverse('inventory:cost_report'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['unknown'],1)
        self.assertContains(response,'Неизвестна')
        self.assertEqual(self.client.get(reverse('inventory:cost_report'),{'start':'invalid'}).context['page'].paginator.count,0)
