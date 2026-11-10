import uuid
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook
from .models import GoodsDocument, Item, StockDocument, StockEntry, DocumentAttachment, MoneyAccount, CashEntry, KaspiOperation
from .bundles import post_bundle, receive_bundle, describe, document_identity, attach
from .costing import available_lots
from .services import balance


class DocumentWorkspaceTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='workspace-admin',password='test')
        self.client.force_login(self.user)
        self.item=Item.objects.create(code='A',name='Товар',brand='AG',article='001',unit='piece')
        self.other=Item.objects.create(code='B',name='Комплект',brand='AG',article='002',unit='set')

    def bundle(self,kind='receipt',warehouse='PP2',destination='',number='N1'):
        return GoodsDocument.objects.create(kind=kind,warehouse=warehouse,destination=destination,date=date.today(),accepted_on=date.today(),number=number,counterparty='Vendor',created_by=self.user)

    def line(self,bundle,item=None,quantity=2,**kwargs):
        return StockDocument.objects.create(bundle=bundle,kind=bundle.kind,item=item or self.item,quantity=quantity,warehouse=bundle.warehouse,destination=bundle.destination,date=bundle.date,external_number=bundle.number,counterparty=bundle.counterparty,unit_cost=Decimal(100),created_by=self.user,**kwargs)

    def test_multi_line_post_is_atomic_and_repeat_cannot_add_stock(self):
        b=self.bundle();self.line(b);self.other.unit='pending';self.other.save();bad=self.line(b,self.other)
        with self.assertRaises(ValidationError):post_bundle(b.pk,self.user)
        self.assertEqual(StockEntry.objects.count(),0)
        self.assertEqual(set(b.lines.values_list('status',flat=True)),{'draft'})
        self.other.unit='set';self.other.save();post_bundle(b.pk,self.user)
        self.assertEqual(balance(self.item,'PP2'),2)
        with self.assertRaises(ValidationError):post_bundle(b.pk,self.user)
        self.assertEqual(StockEntry.objects.count(),2)
        self.assertEqual(describe(b).position_count,2)

    def test_transfer_partial_and_lot_origin_preserved(self):
        opening=self.bundle(warehouse='PP1');origin=self.line(opening,quantity=10);post_bundle(opening.pk,self.user)
        b=self.bundle('transfer','PP1','PP2');line=self.line(b,quantity=6);post_bundle(b.pk,self.user)
        receive_bundle(b.pk,{str(line.pk):'2'},self.user)
        self.assertEqual((balance(self.item,'PP1'),balance(self.item,'PP2'),balance(self.item,'TRANSIT')),(4,2,4))
        self.assertEqual(describe(b).state,'Принято частично')
        lots=available_lots(self.item,include_transit=True)
        self.assertEqual(lots['PP2'][0]['origin'],origin.pk)
        self.assertEqual(lots['TRANSIT'][0]['quantity'],4)
        with self.assertRaises(ValidationError):receive_bundle(b.pk,{str(line.pk):'5'},self.user)
        self.assertEqual(balance(self.item,'PP2'),2)

    def test_attachments_do_not_create_stock_and_are_protected(self):
        b=self.bundle();self.line(b)
        one=attach(b,SimpleUploadedFile('scan.pdf',b'%PDF-example'),self.user)
        two=attach(b,SimpleUploadedFile('scan(1).pdf',b'%PDF-example'),self.user)
        self.assertEqual(one.pk,two.pk);self.assertEqual(DocumentAttachment.objects.count(),1)
        self.assertFalse(StockEntry.objects.exists())
        url=reverse('inventory:attachment',args=[one.pk]);self.assertEqual(self.client.get(url).status_code,200)
        self.client.logout();self.assertEqual(self.client.get(url).status_code,302)

    def test_document_identity_normalizes_case_and_spaces(self):
        a=document_identity('receipt',' N-1 ',date.today(),'Vendor','PP2')
        b=document_identity('receipt','n-1',date.today(),'VENDOR ','PP2')
        self.assertEqual(a,b)

    def test_duplicate_invoice_is_found_even_when_acceptance_date_used(self):
        from .bundles import existing_document
        b=self.bundle();b.date=date.today()-timedelta(days=2);b.save()
        self.assertEqual(existing_document('receipt','n1',date.today(),'vendor','PP2').pk,b.pk)

    def test_new_invoice_pack_conversion_idempotence_and_total(self):
        key=str(uuid.uuid4())
        data={'key':key,'number':'N-21','date':date.today(),'accepted_on':date.today(),'warehouse':'PP2','counterparty':'Vendor','lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-0-item':self.item.pk,'lines-0-packages':'1','lines-0-multiplier':'2','lines-0-cost':'300'}
        url=reverse('inventory:goods_new',args=['receipt'])
        self.assertEqual(self.client.post(url,data).status_code,302)
        b=GoodsDocument.objects.get(number='N-21');line=b.lines.get()
        self.assertEqual((line.quantity,line.unit_cost),(2,Decimal(150)))
        self.assertFalse(StockEntry.objects.exists())
        self.client.post(url,data);self.assertEqual(b.lines.count(),1)
        data['key']=str(uuid.uuid4());self.client.post(url,data);self.assertEqual(GoodsDocument.objects.filter(number='N-21').count(),1)
        post_bundle(b.pk,self.user);self.assertEqual(describe(b).amount,300)

    def test_document_pages_and_hubs_render_without_mutation(self):
        b=self.bundle();self.line(b)
        routes=[('overview',[]),('receipts',[]),('transfers',[]),('journal',[]),('goods_detail',[b.pk]),('item_detail',[self.item.pk]),('reports',[]),('data',[]),('money',[]),('selling',[]),('balances_at',[]),('restore_order',[]),('archive_transfer',[]),('search',[])]
        for name,args in routes:
            with self.subTest(name=name):self.assertEqual(self.client.get(reverse('inventory:'+name,args=args)).status_code,200)
        self.assertFalse(StockEntry.objects.exists())

    def test_selected_warehouse_table_and_summary(self):
        b=self.bundle();self.line(b,quantity=7);post_bundle(b.pk,self.user)
        response=self.client.get(reverse('inventory:items'),{'warehouse':'PP2'})
        self.assertContains(response,'Остаток PP2');self.assertContains(response,'Проведённые документы')
        self.assertNotContains(response,'Открыть остатки по каждому товару')
        response=self.client.get(reverse('inventory:items'),{'warehouse':'PP2','basis':'posted'})
        self.assertEqual(response.context['item_count'],1)

    def test_whole_invoice_payment_counted_once(self):
        b=self.bundle();self.line(b);self.line(b,self.other,quantity=3);post_bundle(b.pk,self.user)
        account=MoneyAccount.objects.create(name='Bank',opening_date=date.today(),created_by=self.user)
        CashEntry.objects.create(account=account,date=date.today(),kind='supplier',amount=150,reference='P1',goods_document=b,created_by=self.user)
        response=self.client.get(reverse('inventory:settlements'))
        self.assertEqual(response.context['total'],500);self.assertEqual(response.context['due'],350)
        self.assertEqual(response.context['page'].paginator.count,1)

    def test_historical_evidence_never_changes_current_stock(self):
        b=self.bundle('transfer','PP1','PP2');b.evidence_only=True;b.source_lines=[{'article':'001','unit':'шт.','planned':20,'accepted':19}];b.save()
        with self.assertRaises(ValidationError):post_bundle(b.pk,self.user)
        response=self.client.get(reverse('inventory:goods_detail',args=[b.pk]))
        self.assertContains(response,'Историческое перемещение');self.assertContains(response,'19')
        self.assertFalse(StockEntry.objects.exists())

    def test_balances_at_use_effective_movement_date(self):
        b=self.bundle();line=self.line(b);post_bundle(b.pk,self.user)
        entry=line.entries.get();entry.effective_on=date.today()-timedelta(days=2);entry.save()
        r=self.client.get(reverse('inventory:balances_at'),{'date':(date.today()-timedelta(days=1)).isoformat()})
        self.assertEqual(r.context['rows'][0]['quantity'],2)

    def test_manager_permission_and_ordinary_user_denial(self):
        from procurement.models import Membership
        user=get_user_model().objects.create_user(username='ordinary',password='test');Membership.objects.create(user=user,role='user');self.client.force_login(user)
        for name in ['receipts','transfers','journal','reports','data','money','selling','search','restore_order','archive_transfer']:
            self.assertEqual(self.client.get(reverse('inventory:'+name)).status_code,403)

    def test_restore_order_checks_control_total_and_does_not_post(self):
        import json
        from procurement.models import Supplier,PurchaseOrder
        supplier=Supplier.objects.create(name='Vendor')
        book=Workbook();sheet=book.active;sheet.append(['№','Артикул','Бренд','Наименование','Количество, шт.','Цена, ₸']);sheet.append([1,'A','AG','Товар',2,100]);stream=BytesIO();book.save(stream)
        data={'number':2,'supplier':supplier.pk,'date':date.today(),'warehouse':'PP2','total':'200','reason':'Восстановление по оригиналу','source':SimpleUploadedFile('order.xlsx',stream.getvalue())}
        response=self.client.post(reverse('inventory:restore_order'),data)
        self.assertEqual(response.status_code,302);self.assertEqual(PurchaseOrder.objects.get(pk=2).total,200)
        self.assertFalse(StockEntry.objects.exists())

    def test_edit_draft_preserves_old_rows_and_post_uses_current_rows_only(self):
        b=self.bundle();old=self.line(b)
        data={'key':str(b.request_key),'version':old.pk,'number':'N-EDIT','date':date.today(),'accepted_on':date.today(),'warehouse':'PP2','counterparty':'Vendor','lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'1','lines-0-item':self.item.pk,'lines-0-packages':'3','lines-0-multiplier':'1','lines-0-cost':'100'}
        response=self.client.post(reverse('inventory:goods_edit',args=[b.pk]),data)
        self.assertEqual(response.status_code,302)
        old.refresh_from_db();self.assertEqual(old.status,'cancelled')
        self.assertEqual(b.lines.count(),2)
        post_bundle(b.pk,self.user);self.assertEqual(balance(self.item,'PP2'),3)
        self.assertEqual(self.client.get(reverse('inventory:goods_edit',args=[b.pk])).status_code,302)
