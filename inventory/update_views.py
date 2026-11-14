"""One upload desk with an owner-bound, expiring review before import."""
from datetime import timedelta
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Min, Max, Count
from django.http import Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .permissions import accounting_access
from .models import ImportBatch,ImportPreview,PreviewFile,StockSnapshot,KaspiOperation,Item,ItemAlias,StockEntry
from .importing import parse_file,import_file

SOURCES=[('pp1','Остатки PP1','Файл PP1 после перемещения: Артикул, PP1 после, шт.'),
         ('pp2','Остатки PP2 · Rapida','Артикул или Штрихкод Rapida, Количество. Можно добавить Название и Бренд.'),
         ('stock','Остатки товаров Kaspi','Выгрузка Kaspi: SKU, model, brand, price, PP1, PP2. Используется количество из PP2.'),
         ('sales','Финансовый отчёт Kaspi','Исходный отчёт Kaspi с датой операции, номером заказа, продажами и комиссиями.')]
LABELS={kind:label for kind,label,_ in SOURCES}


class UpdateForm(forms.Form):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for kind,label,_ in SOURCES:
            self.fields[kind+'_file']=forms.FileField(label='Файл XLSX',required=False,widget=forms.ClearableFileInput(attrs={'accept':'.xlsx'}))
            self.fields[kind+'_date']=forms.DateField(label='Дата данных в файле',required=False,initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    def clean(self):
        data=super().clean();chosen=False
        for kind,_,_ in SOURCES:
            file=data.get(kind+'_file')
            if file:
                chosen=True
                if not data.get(kind+'_date'):self.add_error(kind+'_date','Укажите дату исходных данных.')
                if file.size>10*1024*1024:self.add_error(kind+'_file','Максимальный размер файла — 10 МБ.')
        if not chosen:raise ValidationError('Выберите хотя бы один файл.')
        return data


def review_metrics(kind, rows):
    if kind == 'sales':
        dates=[r['occurred_on'] for r in rows if r.get('occurred_on')]
        revenue=[r['revenue'] for r in rows if r.get('revenue') is not None]
        return {'assortment':len({r['product'] for r in rows if r.get('product')}),
                'quantity':None,'total_amount':sum(revenue,Decimal('0')) if revenue else None,
                'period_start':min(dates) if dates else None,
                'period_end':max(dates) if dates else None,
                'orders':len({r['order_id'] for r in rows if r.get('order_id')})}
    quantities=[r['quantity'] for r in rows if r.get('quantity') is not None]
    complete_qty=len(quantities)==len(rows)
    valued=[Decimal(str(r['price']))*r['quantity'] for r in rows
            if r.get('price') is not None and r.get('quantity') is not None]
    return {'assortment':len({r['sku'] for r in rows}),
            'quantity':sum(quantities) if complete_qty else None,
            'total_amount':sum(valued,Decimal('0')) if len(valued)==len(rows) else None}


def batch_metrics(kind,batch):
    if not batch:return {}
    if kind=='sales':
        qs=KaspiOperation.objects.filter(batch=batch)
        a=qs.aggregate(total_amount=Sum('revenue'),period_start=Min('occurred_on'),period_end=Max('occurred_on'))
        return {**a,'assortment':qs.exclude(product='').values('product').distinct().count(),
                'quantity':None,'orders':qs.values('order_id').distinct().count()}
    qs=StockSnapshot.objects.filter(batch=batch)
    stats=qs.aggregate(assortment=Count('sku',distinct=True),quantity=Sum('quantity'))
    if qs.filter(quantity__isnull=True).exists():stats['quantity']=None
    priced=qs.exclude(quantity__isnull=True).exclude(price__isnull=True).values_list('quantity','price')
    stats['total_amount']=sum((Decimal(q)*p for q,p in priced),Decimal('0')) if not qs.filter(price__isnull=True).exists() else None
    return stats


def file_review(kind,raw):
    rows=parse_file(raw,kind)
    out={'kind':kind,'label':LABELS[kind],'count':len(rows),'rows':rows[:8], 'metrics':review_metrics(kind,rows)}
    if kind=='sales':
        known=set(KaspiOperation.objects.filter(fingerprint__in=[r['fingerprint'] for r in rows]).values_list('fingerprint',flat=True))
        out.update(new=sum(r['fingerprint'] not in known for r in rows),unchanged=sum(r['fingerprint'] in known for r in rows),changed=0,removed=0,conflicts=[])
        return out
    latest=ImportBatch.objects.filter(kind=kind).order_by('-as_of','-pk').first()
    old={s.sku:s for s in StockSnapshot.objects.filter(batch=latest)} if latest else {}
    out.update(new=sum(r['sku'] not in old for r in rows),unchanged=0,changed=0,removed=len(set(old)-{r['sku'] for r in rows}),conflicts=[])
    for r in rows:
        prior=old.get(r['sku'])
        if prior:
            same=(prior.quantity,prior.name,prior.brand,prior.price)==(r['quantity'],r['name'],r['brand'],r['price'])
            out['unchanged' if same else 'changed']+=1
    if kind in ['pp1','pp2']:
        warehouse='PP1' if kind=='pp1' else 'PP2'
        aliases={a.code:a for a in ItemAlias.objects.filter(source='pp1' if kind=='pp1' else 'rapida')}
        articles={}
        for item in Item.objects.all():articles.setdefault(item.article,[]).append(item.pk)
        balances={r['document__item_id']:r['total'] for r in StockEntry.objects.filter(warehouse=warehouse).values('document__item_id').annotate(total=Sum('quantity'))}
        conflicts=[];unmapped=0
        for r in rows:
            alias=aliases.get(r['sku']);ids=[alias.item_id] if alias else articles.get(r['sku'],[])
            if len(ids)!=1:unmapped+=1;continue
            qty=r['quantity']*(alias.multiplier if alias else 1) if r['quantity'] is not None else None
            if ids[0] in balances and qty!=balances[ids[0]]:conflicts.append({'sku':r['sku'],'file':qty,'ledger':balances[ids[0]]})
        out.update(conflicts=conflicts[:20],conflict_count=len(conflicts),unmapped=unmapped)
    return out


def parse_error(exc):
    return isinstance(exc,(ValueError,ArithmeticError,KeyError,IndexError)) or exc.__class__.__module__.startswith(('zipfile','openpyxl','xml'))


@never_cache
@accounting_access
@require_http_methods(['GET','POST'])
def updates(request):
    form=UpdateForm(request.POST or None,request.FILES or None)
    preview=None;reviews=[];expired=False
    token=request.POST.get('preview_id') or request.GET.get('preview')
    if token:
        try:preview=get_object_or_404(ImportPreview,user=request.user,pk=token)
        except (ValueError,ValidationError):raise Http404
        expired=preview.created_at<timezone.now()-timedelta(hours=1) and not preview.committed_at
    if request.method=='POST' and request.POST.get('action')=='confirm' and preview and not expired:
        with transaction.atomic():
            preview=ImportPreview.objects.select_for_update().get(pk=preview.pk,user=request.user)
            if not preview.committed_at:
                files=list(preview.files.all())
                # Validate the entire selection again before any imports are committed.
                for f in files:parse_file(bytes(f.data),f.kind)
                for f in files:
                    batch,count=import_file(bytes(f.data),f.kind,f.filename,f.as_of,request.user)
                    f.result=f'Загрузка №{batch.pk}: новых строк {count}.' if count else f'Файл уже загружен. Новых строк нет (загрузка №{batch.pk}).'
                    f.data=b'';f.save(update_fields=['result','data'])
                preview.committed_at=timezone.now();preview.save(update_fields=['committed_at'])
                messages.success(request,'Выбранные файлы загружены. Складские движения и банковские выплаты автоматически не создавались.')
            else:messages.info(request,'Эта загрузка уже сохранена. Повторных строк не добавлено.')
        return redirect(reverse('inventory:updates')+'?preview='+str(preview.pk))
    # One click: validate all chosen files before committing any source.
    if request.method=='POST' and request.POST.get('action')=='quick' and form.is_valid():
        selected=[]
        for kind,_,_ in SOURCES:
            uploaded=form.cleaned_data.get(kind+'_file')
            if uploaded is None:continue
            raw=uploaded.read()
            try:
                parse_file(raw,kind)
            except Exception as exc:
                if not parse_error(exc):raise
                form.add_error(kind+'_file',f'Файл не принят: {exc}')
                continue
            selected.append((kind,uploaded.name,form.cleaned_data[kind+'_date'],raw))
        if not form.errors and selected:
            with transaction.atomic():
                totals=[]
                for kind,name,as_of,raw in selected:
                    batch,count=import_file(raw,kind,name,as_of,request.user)
                    totals.append(f'{LABELS[kind]}: {count} новых строк')
            messages.success(request,'Проверено и сохранено. '+', '.join(totals)+'. Повторные файлы не дублируются.')
            return redirect('inventory:updates')
    if request.method=='POST' and request.POST.get('action')=='preview' and form.is_valid():
        selected=[]
        for kind,_,_ in SOURCES:
            uploaded=form.cleaned_data.get(kind+'_file')
            if not uploaded:continue
            raw=uploaded.read()
            try:review=file_review(kind,raw)
            except Exception as exc:
                if not parse_error(exc):raise
                form.add_error(kind+'_file',f'Файл не принят: {exc}');continue
            selected.append((kind,uploaded.name,form.cleaned_data[kind+'_date'],raw))
        if not form.errors:
            with transaction.atomic():
                ImportPreview.objects.filter(created_at__lt=timezone.now()-timedelta(days=1)).delete()
                preview=ImportPreview.objects.create(user=request.user)
                for kind,name,as_of,raw in selected:PreviewFile.objects.create(preview=preview,kind=kind,filename=name[:255],as_of=as_of,data=raw)
            return redirect(reverse('inventory:updates')+'?preview='+str(preview.pk))
    if preview and not expired:
        for f in preview.files.all():
            review={'kind':f.kind,'label':LABELS[f.kind]} if preview.committed_at else file_review(f.kind,bytes(f.data))
            review.update(filename=f.filename,as_of=f.as_of,result=f.result)
            latest=ImportBatch.objects.filter(kind=f.kind).order_by('-as_of','-pk').first()
            review['older']=bool(latest and latest.as_of>f.as_of)
            if preview.committed_at:review['metrics']=batch_metrics(f.kind,ImportBatch.objects.filter(kind=f.kind,digest=__import__('hashlib').sha256(bytes(f.data)).hexdigest()).first()) if f.data else batch_metrics(f.kind,latest)
            reviews.append(review)
    blocks=[]
    for kind,label,hint in SOURCES:
        latest=ImportBatch.objects.filter(kind=kind).defer('original_data').order_by('-pk').first()
        count=(KaspiOperation.objects.filter(batch=latest).count() if kind=='sales' else StockSnapshot.objects.filter(batch=latest).count()) if latest else 0
        blocks.append({'kind':kind,'label':label,'hint':hint,'file':form[kind+'_file'],'date':form[kind+'_date'],'latest':latest,'count':count,'focus':request.GET.get('section')==kind,'metrics':batch_metrics(kind,latest)})
    from .check_workspace import analyze
    from django.core.paginator import Paginator
    chosen=request.GET.getlist('source')
    if not chosen:chosen=['pp2','stock']
    analysis=analyze(chosen) if request.GET.get('analyze')=='1' else None
    result_page=Paginator(analysis['comparisons'],60).get_page(request.GET.get('page')) if analysis else None
    return render(request,'inventory/updates.html',{'form':form,'blocks':blocks,'preview':preview,
        'reviews':reviews,'expired':expired,'analysis':analysis,'result_page':result_page,
        'selected_sources':chosen})


@never_cache
@accounting_access
@require_http_methods(['POST'])
def delete_original(request, pk):
    """Delete only stored XLSX bytes; preserve imported rows, snapshots and audit metadata."""
    batch=get_object_or_404(ImportBatch,pk=pk)
    if request.POST.get('confirm') != 'delete_original':
        messages.error(request,'Удаление оригинала не подтверждено.')
        return redirect('inventory:updates')
    if batch.original_data:
        batch.original_data=None
        batch.save(update_fields=['original_data'])
        messages.success(request,f'Оригинал файла «{batch.filename}» удалён. Остатки, операции и история загрузки сохранены.')
    else:
        messages.info(request,'Оригинал уже удалён или не сохранялся.')
    return redirect('inventory:updates')
