"""Post the approved second Omega purchase once, using the existing receiving service."""
from datetime import date
from decimal import Decimal
from uuid import UUID
from django.db import migrations

ROWS = [
(16537,'WXQP','310459','Ролик обводной',1,'4683.53','piece'),
(17682,'EEP','15C270030','Рычаг передний R нижний',2,'11382.01','piece'),
(9531,'WXQP','30849','Колодки передние',1,'4263.02','piece'),
(20863,'EEP','15D120048','Шаровая опора нижняя',2,'2194.51','piece'),
(1738,'EEP','15A130004','Диск тормозной задний (упаковка 2 шт)',1,'16896.79','set'),
(3315,'EEP','15A280007','Ролик натяжной',1,'2675.21','piece'),
(7644,'WXQP','44345','Граната наружняя',2,'6889.04','piece'),
(7665,'EEP','59N200010','Граната наружняя',2,'8118.75','piece'),
(13525,'FREY','765404601','Подшипник подвесной',1,'12248.42','piece'),
(9264,'ATHOS','845500217','Колодки передние',1,'3916.87','piece'),
(3301,'EEP','59F200005','Граната наружняя',2,'7482.24','piece'),
(20755,'SRR','085174','Шаровая опора нижняя (упаковка 2 шт)',1,'4226.02','set'),
(7402,'TMG','4890115020569','Граната наружняя',2,'4202.03','piece'),
]
NUMBER = '00ТК-031583'
ORDER_KEY = UUID('5605a40d-31c5-4d85-8c7b-202611230002')
RECEIPT_KEY = UUID('5605a40d-31c5-4d85-8c7b-202611240002')

def post_omega(apps, schema_editor):
    from procurement.models import PurchaseOrder, Offer
    from inventory.models import GoodsDocument, StockDocument, StockEntry, SupplierItem
    from inventory.purchase_receiving import accept_purchase
    first = PurchaseOrder.objects.select_for_update().filter(pk=2,supplier__name__iexact='Omega').first()
    if first is None:
        return  # This is a production-specific historical import, not demo data.
    assert first.user_id == 104 and first.supplier_id == 1
    assert len(first.items) == 22 and sum(x['quantity'] for x in first.items) == 27
    old = GoodsDocument.objects.get(pk=1, number='00ТК-029813', warehouse='PP2', purchase_order=first)
    docs = list(StockDocument.objects.filter(bundle=old,kind='receipt',status='posted').select_related('item'))
    assert len(docs) == 22 and sum(d.quantity for d in docs) == 27
    assert sum(d.unit_cost*d.quantity for d in docs) == Decimal('141546.00')
    assert all(d.purchase_order_id == first.pk and d.purchase_line is not None for d in docs)
    assert sum(StockEntry.objects.filter(document__bundle=old,warehouse='PP2').values_list('quantity',flat=True)) == 27
    existing = PurchaseOrder.objects.filter(supplier=first.supplier,supplier_order_number=NUMBER)
    if existing.exists():
        raise RuntimeError('Second Omega order already exists; inspect rather than duplicate')
    if GoodsDocument.objects.filter(number__contains='31583').exists() or StockDocument.objects.filter(external_number__contains='31583').exists():
        raise RuntimeError('Second Omega receipt already exists; inspect rather than duplicate')
    items = []
    lines = []
    for pk,brand,article,name,qty,cost,unit in ROWS:
        offer = Offer.objects.get(pk=pk,supplier_id=first.supplier_id,brand=brand,article=article)
        items.append(dict(offer_id=pk,brand=brand,article=article,name=name,quantity=qty,cost=cost,subtotal=str(Decimal(cost)*qty),supplier_url=offer.supplier_url,unit=unit))
        lines.append(dict(quantity=qty,cost=Decimal(cost),unit=unit))
    total=sum(Decimal(x['subtotal']) for x in items)
    assert len(items)==13 and sum(x['quantity'] for x in items)==19 and total==Decimal('129447.02')
    order=PurchaseOrder.objects.create(user=first.user,supplier=first.supplier,title='Омега · Заказ №2 · 23.11.2026',supplier_order_number=NUMBER,request_key=ORDER_KEY,status='confirmed',delivery_stage='waiting',destination='PP2',items=items,original_items=items,estimated_total=total,comment='Источник: заказ поставщика 00ТК-031583 от 23.11.2026; приход по указанию владельца 24.11.2026. Две позиции по 2 детали учитываются комплектами. Сумма строк 129447,02; округлённый итог поставщика 129447.')
    assert accept_purchase(order.pk,first.user,dict(warehouse='PP2',date=date(2026,11,24),waybill=NUMBER,waybill_date=date(2026,11,23),key=RECEIPT_KEY,version=order.version,note='Основание — заказ поставщика, не отдельная накладная. Приход согласован владельцем.'),lines)
    order.refresh_from_db()
    assert order.status=='received'
    # Supplier order is the basis, not a separate waybill.
    PurchaseOrder.objects.filter(pk=order.pk).update(waybill_number='')
    new_docs=StockDocument.objects.filter(purchase_order=order,kind='receipt',status='posted',warehouse='PP2')
    assert new_docs.count()==13 and sum(new_docs.values_list('quantity',flat=True))==19
    assert sum(StockEntry.objects.filter(document__purchase_order=order,warehouse='PP2').values_list('quantity',flat=True))==19
    # Register the already-posted first receipt without generating new movements.
    PurchaseOrder.objects.filter(pk=first.pk).update(status='received',waybill_number=old.number)
    for d in docs:
        source=first.items[d.purchase_line]
        link,created=SupplierItem.objects.get_or_create(supplier=first.supplier,article=source['article'],brand=source['brand'],defaults={'item_id':d.item_id})
        assert link.item_id==d.item_id

class Migration(migrations.Migration):
    dependencies=[('inventory','0017_link_omega_receipt_to_order'),('procurement','0151_supplier_comparison_price_lists')]
    operations=[migrations.RunPython(post_omega,migrations.RunPython.noop)]
