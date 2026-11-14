"""Separate material and financial reports. Neither report posts stock or cash movements."""
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Sum, Count
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from .permissions import accounting_access
from .models import StockEntry, KaspiOperation, StockSnapshot, ImportBatch


def period(request):
    today=date.today()
    start=request.GET.get('start') or (today-timedelta(days=30)).isoformat()
    end=request.GET.get('end') or today.isoformat()
    try:
        a,b=date.fromisoformat(start),date.fromisoformat(end)
        if a>b:raise ValueError()
    except ValueError:
        a,b=today-timedelta(days=30),today
    return a,b


@never_cache
@accounting_access
def material(request):
    start,end=period(request)
    warehouse=request.GET.get('warehouse','PP2')
    if warehouse not in ('PP1','PP2'):warehouse='PP2'
    entries=StockEntry.objects.filter(warehouse=warehouse,effective_on__gte=start,effective_on__lte=end)
    movements=list(entries.values('document__kind').annotate(quantity=Sum('quantity'),lines=Count('id')).order_by('document__kind'))
    types=dict(__import__('inventory.models',fromlist=['StockDocument']).StockDocument.Kind.choices)
    for m in movements:m['title']=types.get(m['document__kind'],m['document__kind'])
    total_delta=sum(m['quantity'] or 0 for m in movements)
    latest=ImportBatch.objects.filter(kind='pp2' if warehouse=='PP2' else 'pp1').defer('original_data').order_by('-as_of','-pk').first()
    snap=StockSnapshot.objects.filter(batch=latest,warehouse=warehouse) if latest else StockSnapshot.objects.none()
    snapshot=snap.aggregate(qty=Sum('quantity'),positions=Count('pk'))
    ops=KaspiOperation.objects.filter(occurred_on__gte=start,occurred_on__lte=end)
    # Financial transactions aren't line items: no quantity can be inferred from a purchase row.
    purchases=ops.filter(operation__icontains='Покупка')
    returns=ops.filter(operation__icontains='Возврат')
    rows=entries.select_related('document__item').order_by('-effective_on','-pk')
    return render(request,'inventory/material.html',dict(start=start,end=end,warehouse=warehouse,
        movements=movements,total_delta=total_delta,snapshot=snapshot,latest=latest,
        purchases_count=purchases.count(),order_count=purchases.values('order_id').distinct().count(),
        returns_count=returns.count(),unknown_quantity=True,
        page=Paginator(rows,40).get_page(request.GET.get('page'))))


@never_cache
@accounting_access
def financial(request):
    start,end=period(request)
    rows=KaspiOperation.objects.filter(occurred_on__gte=start,occurred_on__lte=end)
    fields=('revenue','commission','pay_fee','delivery','product_bonus','review_bonus','other_fees')
    totals=rows.aggregate(**{key:Sum(key) for key in fields})
    known=sum((totals[key] or Decimal('0') for key in fields),Decimal('0'))
    latest=ImportBatch.objects.filter(kind='sales').defer('original_data').order_by('-as_of','-pk').first()
    return render(request,'inventory/financial.html',dict(start=start,end=end,totals=totals,
        known=known,operations=rows.count(),latest=latest,
        page=Paginator(rows.order_by('-occurred_on','-pk'),40).get_page(request.GET.get('page'))))
