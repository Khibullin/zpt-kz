from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum, OuterRef, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from procurement.views import access
from .forms import ItemForm, AliasForm, DocumentForm, AccountForm, CashForm
from .models import Item, ItemAlias, StockDocument, StockEntry, MoneyAccount, CashEntry
from .services import post_document, receive_transfer
from .tools_views import date_filter, xlsx_response

FORMS={'item':(ItemForm,'Новый товар'),'alias':(AliasForm,'Связать код товара'),'document':(DocumentForm,'Новый документ движения товара'),'account':(AccountForm,'Новый денежный счёт'),'cash':(CashForm,'Денежная операция')}

@never_cache
@access('admin')
def create(request, kind):
    if kind not in FORMS: raise Http404
    cls,title=FORMS[kind]
    form=cls(request.POST or None,initial={'date':date.today(),'opening_date':date.today()})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            obj=form.save(commit=False)
            if kind == 'alias':obj.confirmed_by=request.user
            elif kind != 'item':obj.created_by=request.user
            obj.save()
        messages.success(request,'Сохранено. Складской документ необходимо провести отдельным действием.' if kind == 'document' else 'Сохранено.')
        return redirect('inventory:documents' if kind == 'document' else 'inventory:cash' if kind in ['cash','account'] else 'inventory:items')
    return render(request,'inventory/form.html',{'form':form,'title':title})

@never_cache
@access('admin')
def items(request):
    q=request.GET.get('q','').strip()
    rows=Item.objects.prefetch_related('aliases').order_by('code')
    if q: rows=rows.filter(Q(code__icontains=q)|Q(article__icontains=q)|Q(name__icontains=q)|Q(aliases__code__icontains=q)).distinct()
    if request.GET.get('unit'):rows=rows.filter(unit=request.GET['unit'])
    if request.GET.get('brand'):rows=rows.filter(brand=request.GET['brand'])
    warehouses=[request.GET['warehouse']] if request.GET.get('warehouse') in ['PP1','PP2'] else ['PP1','PP2']
    stock_totals=StockEntry.objects.filter(document__item_id=OuterRef('pk'),warehouse__in=warehouses).values('document__item_id').annotate(total=Sum('quantity')).values('total')
    rows=rows.annotate(filtered_stock=Subquery(stock_totals))
    stock_state=request.GET.get('stock_state')
    if stock_state=='positive':rows=rows.filter(filtered_stock__gt=0)
    if stock_state=='zero':rows=rows.filter(filtered_stock=0)
    if stock_state=='unknown':rows=rows.filter(filtered_stock__isnull=True)
    state=request.GET.get('mapped')
    if state=='yes':rows=rows.filter(aliases__isnull=False).distinct()
    if state=='no':rows=rows.filter(aliases__isnull=True)
    rows=rows.order_by(request.GET.get('sort') if request.GET.get('sort') in ['name','-name','code','-code'] else 'code')
    if request.GET.get('export')=='xlsx':
        totals={}
        for r in StockEntry.objects.values('document__item_id','warehouse').annotate(total=Sum('quantity')):totals[(r['document__item_id'],r['warehouse'])]=r['total']
        return xlsx_response('stock-balances',['Код','Артикул','Название','Бренд','Единица','PP1','PP2','В пути','Брак'],([i.code,i.article,i.name,i.brand,i.get_unit_display(),*[totals.get((i.pk,w)) for w in ['PP1','PP2','TRANSIT','DAMAGED']]] for i in rows))
    page=Paginator(rows,50).get_page(request.GET.get('page'))
    balances={}
    ids=[i.pk for i in page]
    for r in StockEntry.objects.filter(document__item_id__in=ids).values('document__item_id','warehouse').annotate(total=Sum('quantity')):
        balances.setdefault(r['document__item_id'],{})[r['warehouse']]=r['total']
    for item in page:
        b=balances.get(item.pk,{})
        item.pp1=b.get('PP1');item.pp2=b.get('PP2');item.transit=b.get('TRANSIT');item.damaged=b.get('DAMAGED')
    query=request.GET.copy();query.pop('page',None)
    return render(request,'inventory/items.html',{'page':page,'q':q,'query':query.urlencode(),'brands':Item.objects.exclude(brand='').values_list('brand',flat=True).distinct().order_by('brand'),'units':Item._meta.get_field('unit').choices})

@never_cache
@access('admin')
def documents(request):
    rows=StockDocument.objects.select_related('item','created_by').prefetch_related('events__user').order_by('-date','-pk')
    for key in ['kind','status','warehouse']:
        if request.GET.get(key):rows=rows.filter(**{key:request.GET[key]})
    q=request.GET.get('q','').strip()
    if q:rows=rows.filter(Q(item__code__icontains=q)|Q(item__name__icontains=q)|Q(counterparty__icontains=q)|Q(external_number__icontains=q))
    rows,filter_form=date_filter(request,rows)
    if request.GET.get('destination'):rows=rows.filter(destination=request.GET['destination'])
    if request.GET.get('export')=='xlsx':return xlsx_response('stock-documents',['№','Дата','Операция','Товар','Склад','Получатель','Количество','Принято','Себестоимость единицы','FIFO всего','Статус','Контрагент','Основание'],([d.pk,d.date,d.get_kind_display(),str(d.item),d.warehouse,d.destination,d.quantity,d.received_quantity,d.unit_cost,d.total_cost,d.get_status_display(),d.counterparty,d.external_number] for d in rows))
    query=request.GET.copy();query.pop('page',None)
    return render(request,'inventory/documents.html',{'page':Paginator(rows,50).get_page(request.GET.get('page')),'q':q,'filter_form':filter_form,'query':query.urlencode(),'kinds':StockDocument.Kind.choices,'statuses':StockDocument._meta.get_field('status').choices})

@never_cache
@access('admin')
@require_POST
def document_action(request,pk):
    try:
        if request.POST.get('action') == 'post':post_document(pk,request.user)
        elif request.POST.get('action') == 'receive':
            raw=request.POST.get('quantity','')
            if not raw.isdigit() or len(raw)>9:raise ValidationError('Укажите целое положительное количество')
            receive_transfer(pk,int(raw),request.user)
        else:raise ValidationError('Неизвестное действие')
        messages.success(request,'Движение товара сохранено.')
    except StockDocument.DoesNotExist:raise Http404
    except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
    return redirect('inventory:documents')

@never_cache
@access('admin')
def cash(request):
    rows=CashEntry.objects.select_related('account','stock_document','cancelled_by').order_by('-date','-pk')
    rows,filter_form=date_filter(request,rows)
    if request.GET.get('state')=='cancelled':rows=rows.filter(cancelled=True)
    elif request.GET.get('state')!='all':rows=rows.filter(cancelled=False)
    q=request.GET.get('q','').strip()
    if q:rows=rows.filter(Q(reference__icontains=q)|Q(counterparty__icontains=q)|Q(category__icontains=q))
    account=request.GET.get('account','')
    if account.isdigit():rows=rows.filter(account_id=account)
    if request.GET.get('kind'):rows=rows.filter(kind=request.GET['kind'])
    accounts=list(MoneyAccount.objects.all().order_by('name'))
    for a in accounts:
        a.current_balance=a.opening_balance
    byid={a.pk:a for a in accounts}
    for row in CashEntry.objects.all():byid[row.account_id].current_balance+=row.signed_amount
    incoming=outgoing=Decimal('0')
    for r in rows:
        if r.signed_amount>0:incoming+=r.signed_amount
        else:outgoing-=r.signed_amount
    if request.GET.get('export')=='xlsx':return xlsx_response('cash-journal',['Дата','Счёт','Операция','Контрагент','Статья','Сумма со знаком','Основание','Отменено','Причина'],([r.date,str(r.account),r.get_kind_display(),r.counterparty,r.category,r.signed_amount,r.reference,r.cancelled,r.cancellation_note] for r in rows))
    query=request.GET.copy();query.pop('page',None)
    return render(request,'inventory/cash.html',{'page':Paginator(rows,50).get_page(request.GET.get('page')),'accounts':accounts,'kinds':CashEntry._meta.get_field('kind').choices,'q':q,'query':query.urlencode(),'filter_form':filter_form,'incoming':incoming,'outgoing':outgoing,'net_cash':incoming-outgoing})

@never_cache
@access('admin')
def cost_report(request):
    from .views import Filters
    form=Filters(request.GET)
    rows=StockDocument.objects.filter(kind__in=['sale','return'],status='posted').select_related('item').order_by('-date','-pk')
    if form.is_valid():
        f=form.cleaned_data
        if f['start']:rows=rows.filter(date__gte=f['start'])
        if f['end']:rows=rows.filter(date__lte=f['end'])
        if f['warehouse']:rows=rows.filter(warehouse=f['warehouse'])
        if f['q']:rows=rows.filter(Q(item__name__icontains=f['q'])|Q(item__article__icontains=f['q'])|Q(external_number__icontains=f['q']))
        if f['start'] and f['end'] and f['start']>f['end']:
            form.add_error('end','Конечная дата раньше начальной');rows=rows.none()
    else:rows=rows.none()
    sums={}
    for kind in ['sale','return']:
        sums[kind]=rows.filter(kind=kind,total_cost__isnull=False).aggregate(total=Sum('total_cost'))['total'] or Decimal('0')
    unknown=rows.filter(total_cost__isnull=True).count()
    damage=rows.filter(kind='return',return_condition='damaged',total_cost__isnull=False).aggregate(total=Sum('total_cost'))['total'] or Decimal('0')
    query=request.GET.copy();query.pop('page',None)
    return render(request,'inventory/cost_report.html',{'form':form,'page':Paginator(rows,50).get_page(request.GET.get('page')),'unknown':unknown,'sold_cost':sums['sale'],'return_cost':sums['return'],'net_cost':sums['sale']-sums['return'],'damage_cost':damage,'query':query.urlencode()})
