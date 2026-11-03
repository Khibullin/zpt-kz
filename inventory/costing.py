"""FIFO replay of immutable stock entries; amounts with missing cost stay unknown."""
from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import ValidationError
from .models import StockEntry

def take(lots, quantity):
    taken=[]
    while quantity:
        if not lots: raise ValidationError('Недостаточно партий для списания. Проверьте начальные остатки.')
        lot=lots[0]
        q=min(quantity,lot['quantity'])
        taken.append({**lot,'quantity':q})
        lot['quantity']-=q;quantity-=q
        if not lot['quantity']:lots.pop(0)
    return taken

def slice_allocations(allocations, start, quantity):
    lots=[dict(lot) for lot in allocations]
    if start: take(lots,start)
    return take(lots,quantity)

def total_cost(allocations):
    if any(lot['unit_cost'] is None for lot in allocations):return None
    return sum((Decimal(lot['unit_cost'])*lot['quantity'] for lot in allocations),Decimal('0'))

def available_lots(item):
    stocks=defaultdict(list)
    transit=defaultdict(list)
    for entry in StockEntry.objects.filter(document__item=item).select_related('document').order_by('pk'):
        doc=entry.document
        if doc.kind in ['opening','receipt','adjustment']:
            stocks[entry.warehouse].append({'origin':doc.pk,'quantity':entry.quantity,'unit_cost':str(doc.unit_cost) if doc.unit_cost is not None else None})
        elif doc.kind == 'transfer':
            if entry.warehouse == doc.warehouse and entry.quantity < 0:
                transit[doc.pk].extend(take(stocks[entry.warehouse],-entry.quantity))
            elif entry.warehouse == doc.destination and entry.quantity > 0:
                stocks[entry.warehouse].extend(take(transit[doc.pk],entry.quantity))
        elif doc.kind == 'supplier_return' and entry.quantity < 0:
            remaining=-entry.quantity
            for lot in stocks[entry.warehouse]:
                if lot['origin']==doc.related_receipt_id:
                    used=min(remaining,lot['quantity']);lot['quantity']-=used;remaining-=used
            if remaining:raise ValidationError('Недостаточно партии исходного прихода для возврата.')
            stocks[entry.warehouse]=[lot for lot in stocks[entry.warehouse] if lot['quantity']]
        elif doc.kind in ['sale','writeoff'] and entry.quantity < 0:
            take(stocks[entry.warehouse],-entry.quantity)
        elif doc.kind == 'return' and entry.quantity > 0:
            stocks[entry.warehouse].extend(dict(lot) for lot in doc.cost_allocations)
    return stocks
