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
from procurement.views import access
from .models import ImportBatch, StockSnapshot, KaspiOperation
from .importing import parse_file, import_file

class UploadForm(forms.Form):
    kind = forms.ChoiceField(label='Тип файла', choices=[('stock','Остатки Kaspi → PP2'),('pp1','Начальные остатки PP1'),('sales','Финансовый отчёт Kaspi')])
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
@access('admin')
def dashboard(request, section='overview'):
    form = Filters(request.GET)
    stocks = StockSnapshot.objects.none()
    pp1 = ImportBatch.objects.filter(kind='pp1').order_by('-as_of','-pk').first()
    latest = ImportBatch.objects.filter(kind='stock').order_by('-as_of','-pk').first()
    if latest:
        stocks = StockSnapshot.objects.filter(batch=latest)
    if pp1:
        stocks = StockSnapshot.objects.filter(batch_id__in=[b.pk for b in [latest,pp1] if b])
    ops = KaspiOperation.objects.all()
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
    query = request.GET.copy(); query.pop('page',None)
    response = render(request,'inventory/dashboard.html',dict(section=section,form=form,latest=latest,pp1=pp1,page=page,query=query.urlencode(),totals=totals,fees=fees,net=net,has_ops=has_ops,stock_count=stocks.count(),batches=ImportBatch.objects.order_by('-pk')[:8]))
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response

@never_cache
@access('admin')
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
