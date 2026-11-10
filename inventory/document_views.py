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
from .permissions import accounting_access
from .forms import ItemForm, AliasForm, DocumentForm, AccountForm, CashForm
from .models import Item, ItemAlias, StockDocument, StockEntry, MoneyAccount, CashEntry
from .services import post_document, receive_transfer
from .tools_views import date_filter, xlsx_response

FORMS={'item':(ItemForm,'Новый товар'),'alias':(AliasForm,'Связать код товара'),'document':(DocumentForm,'Новый документ движения товара'),'account':(AccountForm,'Новый денежный счёт'),'cash':(CashForm,'Денежная операция')}

@never_cache
@accounting_access
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
@accounting_access
def items(request):
    q=request.GET.get('q','').strip()
    rows=Item.objects.prefetch_related('aliases','supplier_links__supplier').order_by('code')
    if q: rows=rows.filter(Q(code__icontains=q)|Q(article__icontains=q)|Q(name__icontains=q)|Q(aliases__code__icontains=q)).distinct()
    if request.GET.get('unit'):rows=rows.filter(unit=request.GET['unit'])
    from .labels import brand_name, supplier_name
    if request.GET.get('brand'):rows=rows.filter(brand=brand_name(request.GET['brand']))
    state=request.GET.get('mapped')
    if state=='yes':rows=rows.filter(aliases__isnull=False).distinct()
    if state=='no':rows=rows.filter(aliases__isnull=True)
    rows=rows.order_by(request.GET.get('sort') if request.GET.get('sort') in ['name','-name','code','-code'] else 'code')
    from .stock_reporting import stock_rows, stock_summary, stock_export, STOCK_HEADERS
    rows=stock_rows(rows,supplier=request.GET.get('supplier',''))
    supplier_options=sorted({name for item in rows for name in item.supplier_names})
    if request.GET.get('supplier'):rows=[i for i in rows if i.matches_supplier]
    basis=request.GET.get('basis','')
    # A combined warehouse selection means either warehouse, not a missing total flag.
    # For a chosen source show only quantities from that source, never a snapshot
    # under a ledger label. The combined number is explicitly a known subtotal.
    if basis in ['posted','snapshot']:
        for item in rows:
            for attr in ['pp1','pp2']:
                posted=getattr(item,attr+'_posted')
                if basis=='posted' and not posted:setattr(item,attr,None)
                elif basis=='snapshot':
                    setattr(item,attr,getattr(item,attr+'_snapshot'))
                    setattr(item,attr+'_source',getattr(item,attr+'_snapshot_source'))
                    setattr(item,attr+'_posted',False)
            known=[v for v in [item.pp1,item.pp2] if v is not None]
            item.total=sum(known) if known else None
            item.partial_total=len(known)==1
    field={'PP1':'pp1','PP2':'pp2','Kaspi':'kaspi'}.get(request.GET.get('warehouse'),'total')
    for item in rows:
        item.filtered_stock=getattr(item,field)
        item.filtered_source=getattr(item,field+'_source','Сумма PP1 и PP2; даты источников могут различаться')
        item.filtered_posted=getattr(item,field+'_posted',False)
    stock_state=request.GET.get('stock_state')
    if stock_state=='positive':rows=[i for i in rows if getattr(i,field) is not None and getattr(i,field)>0]
    if stock_state=='zero':rows=[i for i in rows if getattr(i,field)==0]
    if stock_state=='unknown':rows=[i for i in rows if getattr(i,field) is None]
    if basis=='posted':rows=[i for i in rows if getattr(i,field+'_posted',False)]
    if basis=='snapshot':rows=[i for i in rows if getattr(i,field) is not None]
    summary=stock_summary(rows)
    if request.GET.get('export')=='xlsx':
        if request.GET.get('warehouse') in ['PP1','PP2'] and request.GET.get('compare')!='1':
            return xlsx_response('warehouse-balances',['Артикул','Бренд','Товар','Склад','Количество','Единица','Источник','Поставщик'],([i.article,i.brand,i.name,request.GET['warehouse'],i.filtered_stock,i.get_unit_display(),i.filtered_source,i.supplier_label] for i in rows))
        return xlsx_response('stock-balances',STOCK_HEADERS,stock_export(rows))
    page=Paginator(rows,50).get_page(request.GET.get('page'))
    query=request.GET.copy();query.pop('page',None)
    selected_summary=next((s for s in summary if s['label']=={'PP1':'PP1','PP2':'PP2 · Rapida','Kaspi':'Kaspi'}.get(request.GET.get('warehouse'),'Всего PP1 + PP2')),summary[-1])
    return render(request,'inventory/items.html',{'supplier_options':supplier_options,'selected_supplier':supplier_name(request.GET.get('supplier','')),'selected_brand':brand_name(request.GET.get('brand','')),'selected_summary':selected_summary,'selected_warehouse':request.GET.get('warehouse',''),'compare':request.GET.get('compare')=='1','summary':summary,'item_count':len(rows),'page':page,'q':q,'query':query.urlencode(),'brands':Item.objects.exclude(brand='').values_list('brand',flat=True).distinct().order_by('brand'),'units':Item._meta.get_field('unit').choices})

@never_cache
@accounting_access
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
@accounting_access
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
@accounting_access
def cash(request):
    rows=CashEntry.objects.select_related('account','stock_document','cancelled_by','purchase_order__supplier').order_by('-date','-pk')
    rows,filter_form=date_filter(request,rows)
    if request.GET.get('state')=='cancelled':rows=rows.filter(cancelled=True)
    elif request.GET.get('state')!='all':rows=rows.filter(cancelled=False)
    q=request.GET.get('q','').strip()
    if q:rows=rows.filter(Q(reference__icontains=q)|Q(counterparty__icontains=q)|Q(category__icontains=q)|Q(purchase_order__title__icontains=q))
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
    if request.GET.get('export')=='xlsx':return xlsx_response('cash-journal',['Дата','Счёт','Операция','Контрагент','Статья','Сумма со знаком','Основание','Отменено','Причина','Закупочный заказ'],([r.date,str(r.account),r.get_kind_display(),r.counterparty,r.category,r.signed_amount,r.reference,r.cancelled,r.cancellation_note,r.purchase_order.order_number if r.purchase_order_id else ''] for r in rows))
    query=request.GET.copy();query.pop('page',None)
    return render(request,'inventory/cash.html',{'page':Paginator(rows,50).get_page(request.GET.get('page')),'accounts':accounts,'kinds':CashEntry._meta.get_field('kind').choices,'q':q,'query':query.urlencode(),'filter_form':filter_form,'incoming':incoming,'outgoing':outgoing,'net_cash':incoming-outgoing})

@never_cache
@accounting_access
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
