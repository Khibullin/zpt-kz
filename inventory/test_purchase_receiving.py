import uuid
from datetime import date
from decimal import Decimal
from io import BytesIO
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from openpyxl import load_workbook
from procurement.models import PurchaseOrder, Supplier
from procurement.purchase_history import supplier_history
from procurement.purchase_excel import export_order
from .models import StockDocument, StockEntry, SupplierItem
from .purchase_receiving import accept_purchase


class PurchaseAcceptanceTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='receiver',password='test')
        self.supplier=Supplier.objects.create(name='Omega')
        self.order=PurchaseOrder.objects.create(user=self.user,supplier=self.supplier,request_key=uuid.uuid4(),estimated_total=1000,items=[{'article':'001','brand':'AG','name':'Комплект','quantity':4,'cost':'250'}])
        self.header={'warehouse':'PP2','date':date.today(),'waybill':'NA-1','key':uuid.uuid4(),'version':1}
        self.line={'quantity':2,'cost':Decimal('250'),'unit':'set'}

    def test_partial_supplier_provenance_idempotence_history_and_excel(self):
        self.assertTrue(accept_purchase(self.order.pk,self.user,self.header,[self.line]))
        self.assertFalse(accept_purchase(self.order.pk,self.user,self.header,[self.line]))
        doc=StockDocument.objects.get()
        self.assertEqual((doc.counterparty,doc.warehouse,doc.quantity,doc.status),('Omega','PP2',2,'posted'))
        self.assertEqual(doc.purchase_order,self.order)
        self.assertEqual(doc.item.unit,'set')
        self.assertEqual(StockEntry.objects.count(),1)
        h=supplier_history(self.user,self.supplier.pk)[('001','AG')]
        self.assertEqual((h['received'],h['ordered'],h['pp2']),(2,4,2))
        self.order.refresh_from_db()
        self.header.update(version=self.order.version,key=uuid.uuid4())
        accept_purchase(self.order.pk,self.user,self.header,[self.line])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status,'received')
        self.assertEqual(SupplierItem.objects.count(),1)
        later=PurchaseOrder.objects.create(user=self.user,supplier=self.supplier,request_key=uuid.uuid4(),estimated_total=250,items=self.order.items)
        sheet=load_workbook(export_order(later))['История закупок']
        self.assertEqual(sheet.cell(2,5).value,4)

    def test_overreceipt_stale_version_and_units_rollback(self):
        for patch in [{'quantity':5},{'unit':''}]:
            with self.assertRaises(ValidationError):accept_purchase(self.order.pk,self.user,self.header,[{**self.line,**patch}])
            self.assertFalse(StockEntry.objects.exists())
            self.assertFalse(SupplierItem.objects.exists())
        self.header['version']=2
        with self.assertRaises(ValidationError):accept_purchase(self.order.pk,self.user,self.header,[self.line])

    def test_get_readonly_access_and_post(self):
        self.client.force_login(self.user)
        url=reverse('procurement:purchase_receive',args=[self.order.pk])
        response=self.client.get(url)
        self.assertContains(response,'Поставщик: Omega')
        self.assertContains(response,'PP2 · Rapida')
        self.assertFalse(StockEntry.objects.exists())
        data={'warehouse':'PP2','date':date.today().isoformat(),'waybill':'N1','version':1,'key':str(uuid.uuid4()),'lines-TOTAL_FORMS':1,'lines-INITIAL_FORMS':1,'lines-0-quantity':4,'lines-0-cost':'250','lines-0-unit':'set'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(StockEntry.objects.get().quantity,4)
        other=get_user_model().objects.create_superuser(username='other',password='test')
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code,404)

    def test_supplier_identity_not_merged_across_brands(self):
        accept_purchase(self.order.pk,self.user,self.header,[self.line])
        other=PurchaseOrder.objects.create(user=self.user,supplier=self.supplier,request_key=uuid.uuid4(),estimated_total=250,items=[{'article':'001','brand':'OTHER','name':'Другое','quantity':1,'cost':'250'}])
        self.header.update(key=uuid.uuid4())
        accept_purchase(other.pk,self.user,self.header,[{**self.line,'quantity':1,'unit':'piece'}])
        self.assertEqual(SupplierItem.objects.count(),2)

    def test_order_lines_frozen_after_receipt_but_invoice_can_be_added(self):
        accept_purchase(self.order.pk,self.user,self.header,[self.line])
        self.order.refresh_from_db()
        self.client.force_login(self.user)
        data={'status':'confirmed','version':self.order.version,'supplier_order_number':'OMEGA1','invoice_number':'INV1','waybill_number':'N1','comment':'Received in part','lines-TOTAL_FORMS':1,'lines-INITIAL_FORMS':1,'lines-0-quantity':5,'lines-0-cost':'250'}
        url=reverse('procurement:purchase_detail',args=[self.order.pk])
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.items[0]['quantity'],4)
        data['lines-0-quantity']=4
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.invoice_number,'INV1')

    def test_unprivileged_user_cannot_receive(self):
        from procurement.models import Membership
        person=get_user_model().objects.create_user(username='ordinary',password='test')
        Membership.objects.create(user=person,role='user')
        self.client.force_login(person)
        self.assertEqual(self.client.get(reverse('procurement:purchase_receive',args=[self.order.pk])).status_code,403)
