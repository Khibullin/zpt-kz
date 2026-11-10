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

class MappingTests(TestCase):
    def setUp(self):
        from .models import Item,ImportBatch
        self.user=get_user_model().objects.create_superuser(username='mapping',password='test-password')
        self.item=Item.objects.create(code='FILTER-MAP',article='ABC-123',name='Фильтр')
        self.pp1=ImportBatch.objects.create(kind='pp1',digest='pp1',filename='pp1.xlsx',as_of=date.today(),created_by=self.user)
        self.pp2=ImportBatch.objects.create(kind='stock',digest='pp2',filename='stock.xlsx',as_of=date.today(),created_by=self.user)
        StockSnapshot.objects.create(batch=self.pp1,warehouse='PP1',sku='ABC-123',name='ABC-123',quantity=5)
        self.row=StockSnapshot.objects.create(batch=self.pp2,warehouse='PP2',sku='SKU1',name='Фильтр ABC-123',quantity=2)
    def test_candidates_preserve_suffixes_and_boundaries(self):
        from .matching import candidate_articles
        self.assertEqual(candidate_articles('sku','Фильтр ABC123',['ABC-123','ABC-123A']),['ABC-123'])
        self.assertEqual(candidate_articles('sku','Фильтр XABC123Y',['ABC-123']),[])
        self.assertEqual(candidate_articles('sku','ABC-123A',['ABC-123']),[])
    def test_duplicate_skus_are_flagged_not_summed(self):
        from .matching import reconciliation_rows
        StockSnapshot.objects.create(batch=self.pp2,warehouse='PP2',sku='SKU2',name='Фильтр ABC123',quantity=3)
        rows=[r for r in reconciliation_rows() if r['source']=='kaspi']
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['shared_count']==2 for r in rows))
    def test_confirmation_idempotent_and_no_stock_movement(self):
        from .matching import confirm_mapping
        from .models import StockEntry,Item
        from django.core.exceptions import ValidationError
        _,created=confirm_mapping(self.row.pk,self.item,1,self.user)
        self.assertTrue(created)
        _,created=confirm_mapping(self.row.pk,self.item,1,self.user)
        self.assertFalse(created)
        with self.assertRaises(ValidationError):confirm_mapping(self.row.pk,self.item,2,self.user)
        self.assertFalse(StockEntry.objects.exists())
    def test_stale_snapshot_cannot_be_confirmed(self):
        from .matching import confirm_mapping
        from .models import ImportBatch
        from django.core.exceptions import ValidationError
        ImportBatch.objects.create(kind='stock',digest='new-stock',filename='new.xlsx',as_of=date.today(),created_by=self.user)
        with self.assertRaises(ValidationError):confirm_mapping(self.row.pk,self.item,1,self.user)
    def test_page_and_confirmation_require_explicit_check(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('inventory:reconciliation'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['total'],2)
        response=self.client.post(reverse('inventory:confirm_mapping'),{'snapshot':self.row.pk,'item':self.item.pk,'multiplier':1})
        from .models import ItemAlias
        self.assertFalse(ItemAlias.objects.exists())
    def test_rapida_import_only_reference(self):
        from .models import RapidaReference,StockEntry
        w=Workbook();s=w.active
        s.append(['№','Артикул','По накладной','Принято Rapida','Штрихкод Rapida'])
        s.append([1,'ABC-123',5,4,'00123456789'])
        b=BytesIO();w.save(b)
        _,count=import_file(b.getvalue(),'rapida','rapida.xlsx',date.today(),self.user)
        self.assertEqual(count,1)
        ref=RapidaReference.objects.get()
        self.assertEqual(ref.barcode,'00123456789')
        self.assertEqual(ref.accepted-ref.planned,-1)
        self.assertFalse(StockEntry.objects.exists())
    def test_xlsx_without_dimensions(self):
        from zipfile import ZipFile,ZIP_DEFLATED
        import re
        w=Workbook();s=w.active
        s.append(['№','Артикул','По накладной','Принято Rapida','Штрихкод Rapida'])
        s.append([1,'ABC-123',5,4,'00123456789'])
        original=BytesIO();w.save(original)
        output=BytesIO()
        with ZipFile(BytesIO(original.getvalue())) as source,ZipFile(output,'w',ZIP_DEFLATED) as dest:
            for info in source.infolist():
                content=source.read(info.filename)
                if info.filename=='xl/worksheets/sheet1.xml':content=re.sub(rb'<dimension[^>]*/>',b'',content)
                dest.writestr(info.filename,content)
        from .importing import parse_file
        self.assertEqual(len(parse_file(output.getvalue(),'rapida')),1)
    def test_preparation_is_idempotent_and_does_not_confirm_units(self):
        from .matching import prepare_items
        from .models import Item,ItemAlias,StockEntry
        # Existing hand-created card is retained.
        self.assertEqual(prepare_items(self.user),(0,1))
        source=StockSnapshot.objects.create(batch=self.pp1,warehouse='PP1',sku='NEW-123',name='NEW-123',quantity=3)
        StockSnapshot.objects.create(batch=self.pp2,warehouse='PP2',sku='NEW-SKU',name='Масляный фильтр NEW-123',quantity=1)
        self.assertEqual(prepare_items(self.user),(1,1))
        new=Item.objects.get(article='NEW-123')
        self.assertEqual(new.unit,'pending')
        self.assertEqual(new.name,'Масляный фильтр NEW-123')
        self.assertEqual(new.preparation['pp1_snapshot'],source.pk)
        self.assertEqual(prepare_items(self.user),(0,2))
        self.assertFalse(ItemAlias.objects.exists())
        self.assertFalse(StockEntry.objects.exists())
    def test_pending_item_cannot_post_until_explicit_unit_confirmation(self):
        from .models import Item,StockDocument
        from .services import post_document
        from .matching import confirm_mapping
        from django.core.exceptions import ValidationError
        pending=Item.objects.create(code='PENDING',name='Не проверен',unit='pending')
        doc=StockDocument.objects.create(kind='opening',item=pending,warehouse='PP2',quantity=2,date=date.today(),created_by=self.user)
        with self.assertRaises(ValidationError):post_document(doc.pk,self.user)
        with self.assertRaises(ValidationError):confirm_mapping(self.row.pk,pending,1,self.user)
        confirm_mapping(self.row.pk,pending,1,self.user,unit='piece')
        pending.refresh_from_db();self.assertEqual(pending.unit,'piece')
        post_document(doc.pk,self.user)
        doc.refresh_from_db();self.assertEqual(doc.status,'posted')
    def test_prepared_card_suggested_on_mapping_screen(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('inventory:reconciliation'))
        rows=list(response.context['page'])
        row=next(r for r in rows if r['snapshot'].pk==self.row.pk)
        self.assertEqual(row['form'].initial['item'],self.item)
        self.assertEqual(row['form'].initial['unit'],'piece')
        self.assertContains(response,'Подготовить карточки по PP1')

class AccountingToolsTests(TestCase):
    setUp=StockDocumentsTests.setUp
    document=StockDocumentsTests.document
    def test_opening_preparation_repeat_and_duplicate_skus(self):
        from .models import ImportBatch,ItemAlias,StockDocument,StockEntry
        batch=ImportBatch.objects.create(kind='stock',digest='tools',filename='old.xlsx',as_of=date(2020,1,1),created_by=self.user)
        ItemAlias.objects.create(item=self.item,source='kaspi',code='SKU',multiplier=2,confirmed_by=self.user)
        StockSnapshot.objects.create(batch=batch,warehouse='PP2',sku='SKU',name='Filter',quantity=3)
        url=reverse('inventory:prepare_openings')
        data={'batch':batch.pk,'date':'2026-11-03','checked':'on'}
        self.client.post(url,data);self.client.post(url,data)
        doc=StockDocument.objects.get(kind='opening')
        self.assertEqual(doc.quantity,6);self.assertEqual(doc.status,'draft');self.assertIsNone(doc.unit_cost)
        self.assertFalse(StockEntry.objects.exists())
        ItemAlias.objects.create(item=self.item,source='kaspi',code='SKU2',confirmed_by=self.user)
        StockSnapshot.objects.create(batch=batch,warehouse='PP2',sku='SKU2',name='Filter',quantity=4)
        self.client.post(reverse('inventory:cancel_document',args=[doc.pk]))
        self.client.post(url,data)
        self.assertEqual(StockDocument.objects.count(),1)

    def test_money_transfer_cancel_both_sides_and_duplicate_atomic(self):
        from .models import MoneyAccount,CashEntry
        a=MoneyAccount.objects.create(name='A',opening_balance=100,opening_date=date(2026,1,1),created_by=self.user)
        b=MoneyAccount.objects.create(name='B',opening_balance=200,opening_date=date(2026,1,1),created_by=self.user)
        data={'source':a.pk,'destination':b.pk,'date':'2026-11-03','amount':'50','reference':'TRANSFER'}
        self.client.post(reverse('inventory:transfer_money'),data)
        self.assertEqual(CashEntry.objects.count(),2)
        self.assertEqual(sum(r.signed_amount for r in CashEntry.objects.all()),0)
        self.client.post(reverse('inventory:transfer_money'),data)
        self.assertEqual(CashEntry.objects.count(),2)
        entry=CashEntry.objects.first()
        self.client.post(reverse('inventory:cancel_cash',args=[entry.pk]),{'reason':'Ошибка'})
        self.assertEqual(CashEntry.objects.filter(cancelled=True).count(),2)
        self.assertEqual(sum(r.signed_amount for r in CashEntry.objects.all()),0)
        balances=self.client.get(reverse('inventory:cash')).context['accounts']
        self.assertEqual([a.current_balance for a in balances],[100,200])

    def test_edit_posted_guard_and_unit_lock(self):
        from .services import post_document
        doc=self.document();post_document(doc.pk,self.user)
        self.client.post(reverse('inventory:edit',args=['document',doc.pk]),{'quantity':'99'})
        doc.refresh_from_db();self.assertEqual(doc.quantity,10)
        self.client.post(reverse('inventory:cancel_document',args=[doc.pk]))
        doc.refresh_from_db();self.assertEqual(doc.status,'posted')
        self.client.post(reverse('inventory:edit',args=['item',self.item.pk]),{'code':'FILTER','name':'Updated','unit':'set'})
        self.item.refresh_from_db();self.assertEqual(self.item.unit,'piece');self.assertEqual(self.item.name,'Updated')

    def test_supplier_debt_ignores_cancelled_payments(self):
        from .services import post_document
        from .models import MoneyAccount,CashEntry
        doc=self.document(kind='receipt',unit_cost=100,counterparty='Vendor');post_document(doc.pk,self.user)
        a=MoneyAccount.objects.create(name='A',opening_date=date.today(),created_by=self.user)
        payment=CashEntry.objects.create(account=a,date=date.today(),kind='supplier',amount=400,reference='P',stock_document=doc,created_by=self.user)
        url=reverse('inventory:settlements')
        self.assertEqual(self.client.get(url).context['due'],600)
        self.client.post(reverse('inventory:cancel_cash',args=[payment.pk]),{'reason':'Wrong'})
        self.assertEqual(self.client.get(url).context['due'],1000)

    def test_report_margin_return_and_missing_fields(self):
        from .services import post_document
        receipt=self.document(kind='receipt',warehouse='PP2',unit_cost=100,counterparty='V');post_document(receipt.pk,self.user)
        sale=self.document(kind='sale',warehouse='PP2',quantity=2,external_number='S',sale_amount=1000,selling_costs=50);post_document(sale.pk,self.user)
        ret=self.document(kind='return',warehouse='PP2',quantity=1,related_sale=sale,return_condition='good',sale_amount=500,selling_costs=0);post_document(ret.pk,self.user)
        url=reverse('inventory:operating_report')
        self.assertEqual(self.client.get(url).context['margin'],350)
        type(sale).objects.filter(pk=sale.pk).update(selling_costs=None)
        self.assertIsNone(self.client.get(url).context['margin'])

    def test_exports_safe_and_all_new_views_protected(self):
        import openpyxl
        self.item.name='=HYPERLINK("bad")';self.item.save()
        response=self.client.get(reverse('inventory:items'),{'export':'xlsx'})
        book=openpyxl.load_workbook(BytesIO(response.content));header=next(row[0].row for row in book.active if row[0].value=='Артикул');self.assertEqual(book.active.cell(header+1,3).data_type,'s')
        for route in ['settlements','operating_report','prepare_openings','transfer_money']:
            self.assertEqual(self.client.get(reverse('inventory:'+route)).status_code,200)
        for route in ['items','documents','cash','settlements','operating_report','stock','sales']:
            response=self.client.get(reverse('inventory:'+route),{'export':'xlsx'})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.content.startswith(b'PK'))
        self.client.logout()
        for route in ['settlements','operating_report','prepare_openings','transfer_money']:
            self.assertEqual(self.client.get(reverse('inventory:'+route)).status_code,302)

    def test_invalid_dates_do_not_show_unfiltered_records(self):
        self.document()
        for route in ['documents','cash','settlements','operating_report']:
            self.assertEqual(self.client.get(reverse('inventory:'+route),{'start':'bad'}).context['page'].paginator.count,0)

    def test_inventory_correction_fifo_and_reason(self):
        from .services import post_document,balance
        from django.core.exceptions import ValidationError
        add=self.document(kind='adjustment',quantity=3,unit_cost=100,note='Инвентаризация')
        post_document(add.pk,self.user)
        remove=self.document(kind='writeoff',quantity=2,note='Недостача')
        result=post_document(remove.pk,self.user)
        self.assertEqual(result.total_cost,200);self.assertEqual(balance(self.item,'PP1'),1)
        with self.assertRaises(ValidationError):post_document(self.document(kind='writeoff',quantity=1).pk,self.user)

    def test_financial_clarification_preserves_stock_and_audits_previous(self):
        from .services import post_document,balance
        from .models import StockEvent
        post_document(self.document(kind='receipt',warehouse='PP2',unit_cost=100,counterparty='V').pk,self.user)
        doc=post_document(self.document(kind='sale',warehouse='PP2',quantity=2,external_number='AUDIT').pk,self.user)
        self.client.post(reverse('inventory:financial_details',args=[doc.pk]),{'sale_amount':'1000','selling_costs':'50','reason':'Получен отчёт'})
        doc.refresh_from_db()
        self.assertEqual(doc.total_cost,200);self.assertEqual(doc.sale_amount,1000)
        self.assertEqual(balance(self.item,'PP2'),8)
        self.assertIn('Сумма: None',doc.note)
        self.assertTrue(StockEvent.objects.filter(document=doc,action='Уточнены суммы продажи').exists())

    def test_bank_check_and_stock_filter_multiple_aliases(self):
        from .models import MoneyAccount,CashEntry,ItemAlias
        from .services import post_document
        a=MoneyAccount.objects.create(name='Check',opening_date=date(2026,1,1),opening_balance=100,created_by=self.user)
        CashEntry.objects.create(account=a,date=date(2026,11,3),kind='kaspi',amount=50,reference='X',created_by=self.user)
        result=self.client.get(reverse('inventory:bank_check'),{'account':a.pk,'date':'2026-11-03','actual':150})
        self.assertEqual(result.context['difference'],0)
        for code in ['a','b']:ItemAlias.objects.create(item=self.item,source='kaspi',code=code,confirmed_by=self.user)
        post_document(self.document().pk,self.user)
        response=self.client.get(reverse('inventory:items'),{'warehouse':'PP1','stock_state':'positive','mapped':'yes'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertEqual(list(response.context['page'])[0].filtered_stock,10)
