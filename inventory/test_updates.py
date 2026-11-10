from datetime import date,timedelta
from decimal import Decimal
from io import BytesIO
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook,load_workbook
from .models import Item,GoodsDocument,StockDocument,StockEntry,ImportBatch,StockSnapshot,ImportPreview,PreviewFile,ItemAlias,MoneyAccount,CashEntry
from .bundles import post_bundle,receive_bundle
from .importing import import_file


class BalanceUpdateTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='updates-admin',password='test')
        self.client.force_login(self.user)
        self.item=Item.objects.create(code='O1',article='001',name='Товар',brand='WXQP')
        self.receipt=self.make_doc('receipt','PP2',3,Decimal('100.50'),'Omega')

    def make_doc(self,kind,warehouse,qty,cost=None,supplier='Omega',destination=''):
        b=GoodsDocument.objects.create(kind=kind,warehouse=warehouse,destination=destination,date=date.today(),accepted_on=date.today(),counterparty=supplier,number='N'+str(GoodsDocument.objects.count()),created_by=self.user)
        d=StockDocument.objects.create(bundle=b,kind=kind,item=self.item,warehouse=warehouse,destination=destination,quantity=qty,unit_cost=cost,external_number=b.number,date=date.today(),counterparty=supplier,created_by=self.user)
        post_bundle(b.pk,self.user)
        return d

    def workbook(self,rows):
        b=Workbook();s=b.active
        for row in rows:s.append(row)
        out=BytesIO();b.save(out);return out.getvalue()

    def file(self,raw,name='stock.xlsx'):
        return SimpleUploadedFile(name,raw,content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_supplier_on_other_warehouse_is_empty_and_valued_zero(self):
        r=self.client.get(reverse('inventory:items'),{'supplier':'Омега','warehouse':'PP1'})
        self.assertEqual(r.context['item_count'],0)
        self.assertEqual(r.context['balance_summary']['known_value'],0)
        self.assertContains(r,'На складе PP1 товаров поставщика Омега нет')
        self.assertNotContains(r,'Нет однозначных данных об остатке')
        for wh in ['PP2','']:
            r=self.client.get(reverse('inventory:items'),{'supplier':'Омега','warehouse':wh})
            self.assertEqual(r.context['item_count'],1)
            self.assertEqual(r.context['balance_summary']['known_value'],Decimal('301.50'))
            self.assertEqual(r.context['balance_summary']['unknown'],0)

    def test_value_after_transfer_and_sale_uses_remaining_fifo(self):
        self.make_doc('receipt','PP2',2,Decimal('200.00'))
        self.make_doc('sale','PP2',4)
        r=self.client.get(reverse('inventory:items'),{'warehouse':'PP2','supplier':'Омега'})
        self.assertEqual(r.context['page'][0].filtered_stock,1)
        self.assertEqual(r.context['balance_summary']['known_value'],200)
        d=self.make_doc('transfer','PP2',1,destination='PP1');receive_bundle(d.bundle_id,{str(d.pk):'1'},self.user)
        r=self.client.get(reverse('inventory:items'),{'warehouse':'PP1','supplier':'Омега'})
        self.assertEqual(r.context['balance_summary']['known_value'],200)

    def test_snapshot_sale_price_is_not_purchase_cost(self):
        b=ImportBatch.objects.create(kind='stock',digest='snap',filename='kaspi.xlsx',as_of=date.today(),created_by=self.user)
        StockSnapshot.objects.create(batch=b,warehouse='PP2',sku='001',name='Товар',brand='WXQP',quantity=99,price=10000)
        r=self.client.get(reverse('inventory:items'),{'warehouse':'Kaspi'})
        self.assertEqual(r.context['balance_summary']['known_value'],0)
        self.assertEqual(r.context['balance_summary']['unknown_value'],1)
        self.assertEqual(r.context['page'][0].filtered_stock,99)

    def test_export_has_filters_whole_selection_safe_text_and_typed_values(self):
        self.item.name='=HYPERLINK("evil")';self.item.save()
        r=self.client.get(reverse('inventory:items'),{'supplier':'Омега','warehouse':'PP2','export':'xlsx','sort':'-code','mapped':'no','page':'9'})
        self.assertEqual(r.status_code,200)
        self.assertIn('filename*=',r['Content-Disposition'])
        sheet=load_workbook(BytesIO(r.content)).active
        data=list(sheet.values)
        self.assertIn(('Склад','PP2'),[tuple(row[:2]) for row in data])
        self.assertIn(('Привязка кодов','Коды не привязаны'),[tuple(row[:2]) for row in data])
        hdr=next(n for n,row in enumerate(data,1) if row[0]=='Артикул')
        self.assertEqual(sheet.cell(hdr+1,3).value,self.item.name)
        self.assertEqual(sheet.cell(hdr+1,3).data_type,'s')
        self.assertEqual(sheet.cell(hdr+1,6).value,3)
        self.assertEqual(sheet.cell(hdr+1,8).value,301.5)
        self.assertEqual(sheet.max_row,hdr+1)
        self.assertEqual(sheet.freeze_panes,'F'+str(hdr+1))

    def pp2_raw(self,quantity=8):
        return self.workbook([['Артикул','Название','Количество'],['001','Товар',quantity]])

    def kaspi_raw(self):
        return self.workbook([['SKU','model','brand','price','PP1','PP2'],['001','Товар','WXQP',10000,0,99]])

    def test_multiple_file_preview_confirm_and_duplicate_are_atomic(self):
        url=reverse('inventory:updates');before=StockEntry.objects.count()
        data={'action':'preview','pp2_file':self.file(self.pp2_raw()),'pp2_date':date.today(),'stock_file':self.file(self.kaspi_raw(),'kaspi.xlsx'),'stock_date':date.today()}
        r=self.client.post(url,data);self.assertEqual(r.status_code,302)
        self.assertEqual(ImportBatch.objects.count(),0)
        p=ImportPreview.objects.get();self.assertEqual(p.files.count(),2)
        r=self.client.get(r.url)
        self.assertContains(r,'Расхождений со складским учётом: 1')
        self.assertContains(r,'Загрузить выбранные файлы')
        for _ in range(2):
            r=self.client.post(url,{'action':'confirm','preview_id':p.pk});self.assertEqual(r.status_code,302)
        self.assertEqual(ImportBatch.objects.count(),2)
        self.assertEqual(StockEntry.objects.count(),before)
        self.assertFalse(CashEntry.objects.exists())
        self.assertTrue(all(not f.data for f in p.files.all()))
        r=self.client.get(reverse('inventory:items'),{'warehouse':'PP2','basis':'snapshot'})
        self.assertEqual(r.context['page'][0].filtered_stock,8)
        r=self.client.get(reverse('inventory:items'),{'warehouse':'Kaspi'})
        self.assertEqual(r.context['page'][0].filtered_stock,99)
        self.assertEqual(import_file(self.pp2_raw(),'pp2','again.xlsx',date.today(),self.user)[1],0)

    def test_invalid_file_blocks_all_selected_imports(self):
        r=self.client.post(reverse('inventory:updates'),{'action':'preview','pp2_file':self.file(self.pp2_raw()),'pp2_date':date.today(),'stock_file':self.file(b'broken'),'stock_date':date.today()})
        self.assertEqual(r.status_code,200);self.assertContains(r,'Файл не принят')
        self.assertFalse(ImportBatch.objects.exists());self.assertFalse(ImportPreview.objects.exists())

    def test_review_is_owner_bound_expires_and_requires_preview(self):
        p=ImportPreview.objects.create(user=self.user)
        PreviewFile.objects.create(preview=p,kind='pp2',filename='stock.xlsx',as_of=date.today(),data=self.pp2_raw())
        other=get_user_model().objects.create_superuser(username='other',password='test');self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('inventory:updates'),{'preview':p.pk}).status_code,404)
        self.client.force_login(self.user)
        ImportPreview.objects.filter(pk=p.pk).update(created_at=timezone.now()-timedelta(hours=2))
        r=self.client.post(reverse('inventory:updates'),{'action':'confirm','preview_id':p.pk})
        self.assertContains(r,'Время проверки истекло')
        self.assertFalse(ImportBatch.objects.exists())

    def test_rapida_code_mapping_does_not_create_kaspi_alias(self):
        from .matching import confirm_mapping,reconciliation_rows
        raw=self.workbook([['Штрихкод Rapida','Количество'],['barcode',5]])
        batch,_=import_file(raw,'pp2','rapida.xlsx',date.today(),self.user)
        snap=batch.stocksnapshot_set.get()
        alias,_=confirm_mapping(snap.pk,self.item,1,self.user)
        self.assertEqual(alias.source,'rapida')
        self.assertTrue(any(row['source']=='rapida' for row in reconciliation_rows()))
        r=self.client.get(reverse('inventory:items'),{'warehouse':'PP2','basis':'snapshot'})
        self.assertEqual(r.context['page'][0].filtered_stock,5)
