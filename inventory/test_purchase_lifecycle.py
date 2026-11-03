import uuid
from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import CashEntry, MoneyAccount, StockEntry, StockDocument, PurchaseListing
from .purchasing import order_summary
from .purchase_receiving import accept_purchase
from .costing import available_lots
from procurement.models import PurchaseOrder, Supplier
from procurement.purchase_excel import export_order
from openpyxl import load_workbook


class LifecycleTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='lifecycle',password='test')
        self.client.force_login(self.user)
        self.supplier=Supplier.objects.create(name='Omega')
        self.order=PurchaseOrder.objects.create(user=self.user,supplier=self.supplier,request_key=uuid.uuid4(),estimated_total=1000,items=[{'article':'001','brand':'AG','name':'Комплект','quantity':4,'cost':'250'}])
        self.account=MoneyAccount.objects.create(name='Банк',opening_date=date.today(),opening_balance=10000,created_by=self.user)
    def action(self,action):return reverse('inventory:purchase_action',args=[self.order.pk,action])
    def payment(self,amount=500,kind='supplier',reference='PAY1'):
        return self.client.post(self.action('payment'),{'account':self.account.pk,'date':date.today().isoformat(),'kind':kind,'amount':amount,'reference':reference})
    def receive(self,quantity=2):
        self.order.refresh_from_db()
        accept_purchase(self.order.pk,self.user,{'warehouse':'PP2','date':date.today(),'waybill':'N1','key':uuid.uuid4(),'version':self.order.version},[{'quantity':quantity,'cost':Decimal('250'),'unit':'set'}])
        self.order.refresh_from_db()
    def test_created_order_visible_in_accounting_without_stock(self):
        self.assertContains(self.client.get(reverse('inventory:purchases')),self.order.order_number)
        self.assertFalse(StockEntry.objects.exists())
        self.assertEqual(order_summary(self.order)['remaining'],4)
    def test_plan_and_independent_payment_before_receipt(self):
        tomorrow=date.today()+timedelta(days=1)
        response=self.client.post(self.action('plan'),{'version':1,'title':'Омега · поставка','expected_date':tomorrow.isoformat(),'destination':'PP2','delivery_stage':'waiting'})
        self.assertEqual(response.status_code,302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.expected_date,tomorrow)
        self.assertEqual(self.payment().status_code,302)
        self.assertFalse(StockEntry.objects.exists())
        summary=order_summary(self.order)
        self.assertEqual((summary['paid'],summary['balance'],summary['payment_state']),(500,-500,'Частично оплачено'))
        self.receive()
        summary=order_summary(self.order)
        self.assertEqual((summary['received'],summary['remaining'],summary['balance']),(2,2,0))
        self.assertEqual(summary['delivery_state'],'Принято частично')
        response=self.client.get(reverse('inventory:settlements'))
        self.assertEqual(response.context['paid'],500)
        self.assertEqual(response.context['due'],0)
        self.assertContains(self.client.get(reverse('inventory:cash')),self.order.order_number)
    def test_payment_duplicate_refund_and_cancellation_recomputed(self):
        self.payment(1000)
        self.assertEqual(self.payment(1000).status_code,400)
        self.assertEqual(CashEntry.objects.count(),1)
        self.assertEqual(order_summary(self.order)['payment_state'],'Оплачено')
        self.assertEqual(self.payment(200,'supplier_refund','RET1').status_code,302)
        self.assertEqual(order_summary(self.order)['paid'],800)
        self.assertEqual(CashEntry.objects.get(kind='supplier_refund').signed_amount,200)
        self.assertEqual(self.payment(900,'supplier_refund','RET2').status_code,400)
        entry=CashEntry.objects.get(kind='supplier')
        self.client.post(reverse('inventory:cancel_cash',args=[entry.pk]),{'reason':'Ошибочная запись'})
        self.assertEqual(order_summary(self.order)['paid'],-200)
    def test_soft_delete_restore_and_line_remove(self):
        self.assertEqual(self.client.get(self.action('delete')).status_code,200)
        self.assertFalse(self.order.archived)
        self.client.post(self.action('delete'),{'version':1})
        self.order.refresh_from_db();self.assertTrue(self.order.archived)
        self.assertNotContains(self.client.get(reverse('inventory:purchases')),self.order.order_number)
        self.assertContains(self.client.get(reverse('inventory:purchases'),{'deleted':1}),self.order.order_number)
        self.client.post(self.action('restore'),{'version':self.order.version})
        self.order.refresh_from_db();self.assertFalse(self.order.archived)
        self.client.post(self.action('remove'),{'version':self.order.version,'line':0})
        self.order.refresh_from_db();self.assertEqual(self.order.total,0)
        self.assertEqual(self.order.original_items,[])
        sheet=load_workbook(export_order(self.order)).active
        self.assertNotIn('001',[r[1] for r in sheet.iter_rows(values_only=True)])
    def test_cannot_delete_or_remove_financial_or_posted_order(self):
        self.payment()
        self.order.refresh_from_db()
        self.client.post(self.action('delete'),{'version':self.order.version})
        self.client.post(self.action('remove'),{'version':self.order.version,'line':0})
        self.order.refresh_from_db();self.assertFalse(self.order.archived);self.assertEqual(self.order.total,1000)
        self.receive()
        self.client.post(self.action('delete'),{'version':self.order.version})
        self.order.refresh_from_db();self.assertFalse(self.order.archived)
    def test_return_reduces_exact_lot_and_debt_without_erasing_receipt(self):
        self.receive(4)
        receipt=StockDocument.objects.get(kind='receipt')
        response=self.client.post(self.action('return'),{'receipt':receipt.pk,'warehouse':'PP2','quantity':1,'date':date.today().isoformat(),'reason':'Брак','version':self.order.version})
        self.assertEqual(response.status_code,302)
        self.assertEqual(sum(e.quantity for e in StockEntry.objects.all()),3)
        self.assertEqual(available_lots(receipt.item)['PP2'][0]['quantity'],3)
        summary=order_summary(self.order)
        self.assertEqual((summary['received'],summary['returned'],summary['balance']),(4,1,750))
        self.order.refresh_from_db()
        self.assertEqual(self.client.post(self.action('return'),{'receipt':receipt.pk,'warehouse':'PP2','quantity':4,'date':date.today().isoformat(),'reason':'Брак','version':self.order.version}).status_code,400)
        self.assertEqual(StockDocument.objects.filter(kind='supplier_return').count(),1)
    def test_listing_requires_stock_price_and_confirmation(self):
        url=reverse('inventory:purchase_listing',args=[self.order.pk,0])
        data={'state':'listed','price':'1000','quantity':1,'url':'https://kaspi.kz/shop/p/example-123/','checked_on':date.today().isoformat()}
        self.assertEqual(self.client.post(url,data).status_code,400)
        self.receive()
        self.assertEqual(self.client.post(url,{**data,'url':'https://example.com'}).status_code,400)
        self.assertEqual(self.client.post(url,{**data,'quantity':3}).status_code,400)
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(PurchaseListing.objects.get().state,'listed')
        self.assertEqual(StockEntry.objects.count(),1)
    def test_cancel_keeps_payment_but_removes_expected_quantity(self):
        self.payment()
        self.order.refresh_from_db()
        data={'version':self.order.version,'title':'','expected_date':'','destination':'PP2','delivery_stage':'cancelled','reason':'Поставщик отказал'}
        self.assertEqual(self.client.post(self.action('plan'),data).status_code,302)
        self.order.refresh_from_db()
        summary=order_summary(self.order)
        self.assertEqual((summary['remaining'],summary['paid'],summary['balance']),(0,500,-500))
        self.assertEqual(self.payment(100,reference='PAY2').status_code,400)
        self.assertEqual(self.payment(500,'supplier_refund','REF').status_code,302)
    def test_scope_and_stale_delete(self):
        self.assertEqual(self.client.post(self.action('delete'),{'version':999}).status_code,302)
        self.order.refresh_from_db();self.assertFalse(self.order.archived)
        other=get_user_model().objects.create_superuser(username='different',password='test')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.action('payment')).status_code,404)
        self.assertNotContains(self.client.get(reverse('inventory:purchases')),self.order.order_number)
