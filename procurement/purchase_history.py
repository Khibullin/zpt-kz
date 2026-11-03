"""Supplier-specific history available before a repeat purchase."""
from collections import defaultdict
from django.db.models import Sum
from inventory.models import StockDocument, SupplierItem, StockEntry
from .models import PurchaseOrder


def supplier_history(user, supplier_id, before_pk=None):
    result=defaultdict(lambda:{'ordered':0,'received':0,'last_date':None,'pp2':None})
    orders=PurchaseOrder.objects.filter(user=user,supplier_id=supplier_id).exclude(status='cancelled')
    if before_pk is not None:orders=orders.filter(pk__lt=before_pk)
    for order in orders:
        for line in order.items:
            result[(line['article'],line['brand'])]['ordered']+=line['quantity']
    receipts=StockDocument.objects.filter(purchase_order__in=orders,status='posted',kind='receipt').select_related('purchase_order','item')
    for doc in receipts:
        source=doc.purchase_order.items[doc.purchase_line]
        entry=result[(source['article'],source['brand'])]
        entry['received']+=doc.quantity
        entry['last_date']=max(entry['last_date'],doc.date) if entry['last_date'] else doc.date
    totals={r['document__item_id']:r['total'] for r in StockEntry.objects.filter(warehouse='PP2').values('document__item_id').annotate(total=Sum('quantity'))}
    for link in SupplierItem.objects.filter(supplier_id=supplier_id):
        result[(link.article,link.brand)]['pp2']=totals.get(link.item_id)
    return result
