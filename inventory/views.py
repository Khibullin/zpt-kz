from datetime import date
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .permissions import accounting_access
from .models import ImportBatch, StockSnapshot, KaspiOperation, MoneyAccount, CashEntry, StockDocument
from .importing import parse_file, import_file

class UploadForm(forms.Form):
    kind = forms.ChoiceField(label='Тип файла', choices=[('stock','Остатки Kaspi → PP2'),('pp1','Начальные остатки PP1'),('sales','Финансовый отчёт Kaspi'),('rapida','Акт Rapida · штрихкоды и расхождения')])
    as_of = forms.DateField(label='Дата состояния / отчёта', widget=forms.DateInput(attrs={'type':'date'}), initial=date.today)
    file = forms.FileField(label='Файл XLSX')

class Filters(forms.Form):
    q = forms.CharField(label='Поиск', required=False)
    start = forms.DateField(label='С даты', required=False, widget=forms.DateInput(attrs={'type':'date'}))
    end = forms.DateField(label='По дату', required=False, widget=forms.DateInput(attrs={'type':'date'}))
    warehouse = forms.ChoiceField(label='Склад', required=False, choices=[('','Все'),('PP1','PP1 · Asia Grade'),('PP2','PP2 · Rapida')])
    state = forms.ChoiceField(label='Остаток', required=False, choices=[('','Все'),('positive','В наличии'),('zero','Нулевой'),('unknown','Неизвестный')])
    sort = forms.ChoiceField(label='Сортировка', required=False, choices=[('','По умолчанию'),('name','Название'),('quantity','Количество ↑'),('-quantity','Количество ↓'),('revenue','Выручка ↑'),('-revenue','Выручка ↓')])

@never_cache
@accounting_access
def dashboard(request, section='overview'):
    if section == 'finance':
        section = 'sales'  # Compatibility URL; the menu has one Kaspi report.
    if section == 'stock' and request.GET.get('raw') != '1':
        from .document_views import items
        params=request.GET.copy()
        if 'state' in params:params['stock_state']=params['state']
        request.GET=params
        return items(request)
    form = Filters(request.GET)
    stocks = StockSnapshot.objects.none()
    pp1 = ImportBatch.objects.filter(kind='pp1').order_by('-as_of','-pk').first()
    latest = ImportBatch.objects.filter(kind='stock').order_by('-as_of','-pk').first()
    if latest:
        stocks = StockSnapshot.objects.filter(batch=latest)
    if pp1:
        stocks = StockSnapshot.objects.filter(batch_id__in=[b.pk for b in [latest,pp1] if b])
    ops = KaspiOperation.objects.select_related('reconciliation__document__bundle').all()
    valid = form.is_valid()
    if valid:
        f = form.cleaned_data
        if f['q']:
            stocks = stocks.filter(Q(sku__icontains=f['q'])|Q(name__icontains=f['q'])|Q(brand__icontains=f['q']))
            ops = ops.filter(Q(order_id__icontains=f['q'])|Q(product__icontains=f['q']))
        if f['warehouse']:
            stocks = stocks.filter(warehouse=f['warehouse'])
            if f['warehouse'] == 'PP1':
                ops = ops.none()
        if f['state'] == 'positive': stocks = stocks.filter(quantity__gt=0)
        if f['state'] == 'zero': stocks = stocks.filter(quantity=0)
        if f['state'] == 'unknown': stocks = stocks.filter(quantity__isnull=True)
        if f['start']: ops = ops.filter(occurred_on__gte=f['start'])
        if f['end']: ops = ops.filter(occurred_on__lte=f['end'])
        if f['start'] and f['end'] and f['start'] > f['end']:
            form.add_error('end','Конечная дата раньше начальной')
            ops = ops.none()
    else:
        stocks, ops = stocks.none(), ops.none()
    stock_sort = form.cleaned_data.get('sort') if valid else ''
    stocks = stocks.select_related('batch').order_by(stock_sort if stock_sort in ['name','quantity','-quantity'] else 'sku','pk')
    ops = ops.order_by(stock_sort if stock_sort in ['revenue','-revenue'] else '-occurred_on','pk')
    totals = ops.aggregate(**{k:Sum(k) for k in ['revenue','commission','pay_fee','delivery','product_bonus','review_bonus','other_fees']})
    has_ops = ops.exists()
    fees = sum((totals[k] or Decimal('0') for k in totals if k != 'revenue'),Decimal('0')) if has_ops else None
    net = (totals['revenue'] or 0) + fees if has_ops else None
    data = ops if section in ['sales','finance'] else stocks
    page = Paginator(data,50).get_page(request.GET.get('page'))
    from .labels import brand_name
    if section=='stock':
        for row in page: row.brand=brand_name(row.brand)
    query = request.GET.copy(); query.pop('page',None)
    cash_accounts = list(MoneyAccount.objects.order_by('name'))
    accounts_by_id = {a.pk:a for a in cash_accounts}
    for a in cash_accounts: a.current_balance = a.opening_balance
    for entry in CashEntry.objects.all(): accounts_by_id[entry.account_id].current_balance += entry.signed_amount
    receipt_debt = Decimal('0')
    for receipt in StockDocument.objects.filter(kind='receipt',status='posted',purchase_order__isnull=True):
        paid = CashEntry.objects.filter(stock_document=receipt,kind='supplier',cancelled=False).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        receipt_debt += receipt.quantity * receipt.unit_cost - paid
    from procurement.models import PurchaseOrder
    from .purchasing import order_summary
    for purchase in PurchaseOrder.objects.filter(archived=False):
        receipt_debt += order_summary(purchase)['balance']
    if request.GET.get('export')=='xlsx':
        from .tools_views import xlsx_response
        if section == 'overview':
            from .document_views import items
            params=request.GET.copy()
            if 'state' in params:params['stock_state']=params['state']
            request.GET=params
            return items(request)
        if section in ['sales','finance']:return xlsx_response('kaspi-operations',['Дата','Заказ','Операция','Товар','Выручка','Комиссия','Kaspi Pay','Доставка','Бонусы товара','Бонусы отзыва','Прочее','После расходов'],([r.occurred_on,r.order_id,r.operation,r.product,r.revenue,r.commission,r.pay_fee,r.delivery,r.product_bonus,r.review_bonus,r.other_fees,r.after_fees] for r in ops))
        return xlsx_response('stock-snapshots',['SKU','Название','Бренд','Склад','Количество предложения','Цена','Дата снимка'],([r.sku,r.name,brand_name(r.brand),r.warehouse,r.quantity,r.price,r.batch.as_of] for r in stocks))
    from .stock_reporting import stock_rows, stock_summary
    overview_items=stock_rows() if section == 'overview' else []
    if section == 'overview' and valid:
        search=form.cleaned_data['q'].casefold()
        if search:overview_items=[i for i in overview_items if search in (' '.join([i.code,i.article,i.name,i.brand])).casefold()]
        field={'PP1':'pp1','PP2':'pp2'}.get(form.cleaned_data['warehouse'],'total')
        state=form.cleaned_data['state']
        if state=='positive':overview_items=[i for i in overview_items if getattr(i,field) is not None and getattr(i,field)>0]
        if state=='zero':overview_items=[i for i in overview_items if getattr(i,field)==0]
        if state=='unknown':overview_items=[i for i in overview_items if getattr(i,field) is None]
    elif section == 'overview':overview_items=[]
    response = render(request,'inventory/dashboard.html',dict(summary=stock_summary(overview_items),item_count=len(overview_items),cash_accounts=cash_accounts,receipt_debt=receipt_debt,section=section,form=form,latest=latest,pp1=pp1,page=page,query=query.urlencode(),totals=totals,fees=fees,net=net,has_ops=has_ops,stock_count=stocks.count(),batches=ImportBatch.objects.order_by('-pk')[:8]))
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response

@never_cache
@accounting_access
@require_http_methods(['GET','POST'])
def upload(request):
    form = UploadForm(request.POST or None,request.FILES or None)
    preview = None
    if request.method == 'POST' and form.is_valid():
        try:
            uploaded = form.cleaned_data['file']
            if uploaded.size > 10*1024*1024:
                raise ValueError('Максимальный размер — 10 МБ')
            raw = uploaded.read()
            parsed = parse_file(raw,form.cleaned_data['kind'])
            if request.POST.get('confirm') == 'yes':
                batch,count = import_file(raw,form.cleaned_data['kind'],uploaded.name,form.cleaned_data['as_of'],request.user)
                messages.success(request,f'Загрузка №{batch.pk}: новых строк {count}. Повторные операции не добавляются.')
                return redirect('inventory:overview')
            preview = {'count':len(parsed),'rows':parsed[:5]}
        except Exception as exc:
            if isinstance(exc, (ValueError,ArithmeticError,KeyError,IndexError)) or exc.__class__.__module__.startswith(('zipfile','openpyxl','xml')):
                form.add_error(None,f'Файл не принят: {exc}')
            else:
                raise
    return render(request,'inventory/upload.html',dict(form=form,preview=preview))
