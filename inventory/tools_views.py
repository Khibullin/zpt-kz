"""Daily accounting tools. Imports remain evidence until a draft is posted."""
from datetime import date
from decimal import Decimal
from io import BytesIO
import uuid
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.models import Q, Sum
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from .permissions import accounting_access
from .models import Item, ItemAlias, StockDocument, StockEntry, StockEvent, MoneyAccount, CashEntry, StockSnapshot
from .forms import ItemForm, DocumentForm, MoneyTransferForm, OpeningForm
from .views import Filters


def xlsx_response(filename, headers, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    book=Workbook(); sheet=book.active; sheet.title='Учёт'
    sheet.append(headers)
    for row in rows:
        sheet.append(list(row))
        # External names and document numbers must stay text, never Excel formulas.
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value,str):cell.data_type='s'
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='087C50')
    for i in range(1,len(headers)+1):sheet.column_dimensions[get_column_letter(i)].width=24
    output=BytesIO();book.save(output)
    response=HttpResponse(output.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition']=f'attachment; filename="{filename}.xlsx"'
    return response


def date_filter(request, rows, field='date'):
    form=Filters(request.GET)
    if not form.is_valid():return rows.none(),form
    f=form.cleaned_data
    if f['start'] and f['end'] and f['start']>f['end']:
        form.add_error('end','Конечная дата раньше начальной');return rows.none(),form
    if f['start']:rows=rows.filter(**{field+'__gte':f['start']})
    if f['end']:rows=rows.filter(**{field+'__lte':f['end']})
    return rows,form


@never_cache
@accounting_access
def edit(request, kind, pk):
    if kind not in ['item','document']:raise Http404
    model,cls=(Item,ItemForm) if kind=='item' else (StockDocument,DocumentForm)
    # Lock through save, including the status/unit guard, so posting cannot race edits.
    with transaction.atomic():
        obj=get_object_or_404(model.objects.select_for_update(),pk=pk)
        if kind=='document' and obj.status!='draft':
            messages.error(request,'Редактировать можно только черновик.');return redirect('inventory:documents')
        old_unit=obj.unit if kind=='item' else None
        form=cls(request.POST or None,instance=obj)
        if kind=='item' and StockEntry.objects.filter(document__item=obj).exists():form.fields['unit'].disabled=True
        if request.method=='POST' and form.is_valid():
            form.save()
            if kind=='document':StockEvent.objects.create(document=obj,action='Изменён черновик',quantity=obj.quantity,user=request.user)
            messages.success(request,'Изменения сохранены.')
            return redirect('inventory:items' if kind=='item' else 'inventory:documents')
    return render(request,'inventory/form.html',{'form':form,'title':'Изменить товар' if kind=='item' else f'Изменить черновик №{pk}'})


@never_cache
@accounting_access
@require_POST
def cancel_document(request,pk):
    with transaction.atomic():
        doc=get_object_or_404(StockDocument.objects.select_for_update(),pk=pk)
        if doc.status!='draft':messages.error(request,'Отменить можно только черновик. Проведённое движение сохраняется в истории.')
        else:
            doc.status='cancelled';doc.save(update_fields=['status'])
            StockEvent.objects.create(document=doc,action='Отменён черновик',quantity=doc.quantity,user=request.user)
            messages.success(request,'Черновик отменён. Остатки не изменены.')
    return redirect('inventory:documents')


@never_cache
@accounting_access
def transfer_money(request):
    form=MoneyTransferForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        try:
            with transaction.atomic():
                list(MoneyAccount.objects.select_for_update().filter(pk__in=[d['source'].pk,d['destination'].pk]).order_by('pk'))
                key=str(uuid.uuid4())
                for account,kind in [(d['source'],'transfer_out'),(d['destination'],'transfer_in')]:
                    CashEntry.objects.create(account=account,kind=kind,date=d['date'],amount=d['amount'],reference=d['reference'],note=d['note'],transfer_key=key,created_by=request.user)
            messages.success(request,'Перевод записан по двум счетам. Доход и расход не создаются.');return redirect('inventory:cash')
        except IntegrityError:form.add_error('reference','Этот номер уже использован на одном из счетов. Перевод не записан.')
    return render(request,'inventory/form.html',{'form':form,'title':'Перевод между своими счетами'})


@never_cache
@accounting_access
@require_POST
def cancel_cash(request,pk):
    reason=request.POST.get('reason','').strip()
    if not reason:messages.error(request,'Укажите причину отмены.');return redirect('inventory:cash')
    with transaction.atomic():
        entry=get_object_or_404(CashEntry,pk=pk)
        rows=CashEntry.objects.filter(transfer_key=entry.transfer_key) if entry.transfer_key else CashEntry.objects.filter(pk=pk)
        # Both sides of an internal transfer are cancelled atomically.
        list(rows.select_for_update().order_by('pk'))
        count=rows.filter(cancelled=False).update(cancelled=True,cancellation_note=reason,cancelled_by=request.user,cancelled_at=timezone.now())
    messages.success(request,f'Отменено записей: {count}. История сохранена.')
    return redirect('inventory:cash')


@never_cache
@accounting_access
def prepare_openings(request):
    form=OpeningForm(request.POST or None);created=0;skipped=[]
    if request.method=='POST' and form.is_valid():
        data=form.cleaned_data;batch=data['batch']
        source='pp1' if batch.kind=='pp1' else 'kaspi'
        with transaction.atomic():
            list(Item.objects.select_for_update().order_by('pk').values_list('pk',flat=True))
            aliases={a.code:a for a in ItemAlias.objects.filter(source=source).select_related('item')}
            snapshots=list(StockSnapshot.objects.filter(batch=batch).order_by('pk'))
            counts={}
            for row in snapshots:
                alias=aliases.get(row.sku)
                if alias:counts[alias.item_id]=counts.get(alias.item_id,0)+1
            for row in snapshots:
                alias=aliases.get(row.sku)
                reason=''
                if not alias:reason='нет подтверждённой связи'
                elif alias.item.unit=='pending':reason='не подтверждена единица'
                elif counts[alias.item_id]>1:reason='несколько SKU одного товара: требуется разделение запаса'
                elif row.quantity is None:reason='количество не заполнено'
                elif row.quantity==0:reason='нулевое количество'
                elif StockEntry.objects.filter(document__item=alias.item,warehouse=row.warehouse).exists():reason='остаток уже установлен или были движения'
                elif StockDocument.objects.filter(item=alias.item,warehouse=row.warehouse,kind='opening').exclude(status='cancelled').exists():reason='черновик уже существует'
                if reason:skipped.append(f'{row.sku}: {reason}');continue
                doc=StockDocument.objects.create(kind='opening',item=alias.item,warehouse=row.warehouse,quantity=row.quantity*alias.multiplier,date=data['date'],external_number=f'Импорт №{batch.pk}',note=f'Черновик из {batch.filename}, состояние на {batch.as_of}. Себестоимость уточнить.',created_by=request.user)
                StockEvent.objects.create(document=doc,action='Подготовлен из файла',quantity=doc.quantity,user=request.user)
                created+=1
        messages.success(request,f'Подготовлено черновиков: {created}. Пропущено строк: {len(skipped)}. Движений товара не создано.')
    return render(request,'inventory/openings.html',{'form':form,'skipped':skipped,'created':created})


@never_cache
@accounting_access
def settlements(request):
    rows=StockDocument.objects.filter(kind='receipt',status='posted',purchase_order__isnull=True).select_related('item').order_by('-date','-pk')
    rows,form=date_filter(request,rows)
    q=request.GET.get('q','').strip()
    if q:rows=rows.filter(Q(counterparty__icontains=q)|Q(external_number__icontains=q)|Q(item__article__icontains=q))
    payments=dict(CashEntry.objects.filter(cancelled=False,kind='supplier',stock_document__isnull=False).values('stock_document_id').annotate(total=Sum('amount')).values_list('stock_document_id','total'))
    result=[];total=paid=Decimal('0')
    for doc in rows:
        doc.amount=doc.quantity*doc.unit_cost;doc.paid=payments.get(doc.pk,Decimal('0'));doc.due=doc.amount-doc.paid
        state=request.GET.get('payment','')
        if state=='unpaid' and doc.due<=0:continue
        if state=='paid' and doc.due!=0:continue
        if state=='overpaid' and doc.due>=0:continue
        result.append(doc);total+=doc.amount;paid+=doc.paid
    from procurement.models import PurchaseOrder
    from .purchasing import order_summary
    from types import SimpleNamespace
    orders=PurchaseOrder.objects.filter(archived=False).select_related('supplier')
    if form.is_valid():
        if form.cleaned_data.get('start'):orders=orders.filter(created_at__date__gte=form.cleaned_data['start'])
        if form.cleaned_data.get('end'):orders=orders.filter(created_at__date__lte=form.cleaned_data['end'])
    else:orders=orders.none()
    if q:orders=orders.filter(Q(supplier__name__icontains=q)|Q(title__icontains=q)|Q(items__icontains=q))
    for order in orders:
        summary=order_summary(order)
        if not summary['received'] and not summary['paid']:continue
        due=summary['balance'];state=request.GET.get('payment','')
        if state=='unpaid' and due<=0 or state=='paid' and due!=0 or state=='overpaid' and due>=0:continue
        result.append(SimpleNamespace(pk=order.order_number,date=order.created_at.date(),counterparty=order.supplier.name,external_number=order.display_title,item='Закупочный заказ · все приходы и платежи',amount=summary['receipt_value'],paid=summary['paid'],due=due))
        total+=summary['receipt_value'];paid+=summary['paid']
    if request.GET.get('export')=='xlsx':return xlsx_response('supplier-settlements',['Документ','Дата','Поставщик','Основание','Товар','Приход, ₸','Оплачено, ₸','Сальдо, ₸'],([d.pk,d.date,d.counterparty,d.external_number,str(d.item),d.amount,d.paid,d.due] for d in result))
    unlinked=CashEntry.objects.filter(kind='supplier',cancelled=False,stock_document__isnull=True,purchase_order__isnull=True).aggregate(total=Sum('amount'))['total']
    query=request.GET.copy();query.pop('page',None)
    return render(request,'inventory/settlements.html',{'form':form,'page':Paginator(result,50).get_page(request.GET.get('page')),'total':total,'paid':paid,'due':total-paid,'unlinked':unlinked,'query':query.urlencode()})


@never_cache
@accounting_access
def operating_report(request):
    docs,form=date_filter(request,StockDocument.objects.filter(kind__in=['sale','return'],status='posted').select_related('item'))
    q=request.GET.get('q','').strip()
    if q:docs=docs.filter(Q(item__name__icontains=q)|Q(item__article__icontains=q)|Q(external_number__icontains=q))
    if request.GET.get('warehouse'):docs=docs.filter(warehouse=request.GET['warehouse'])
    rows=[];revenue=cost=expenses=Decimal('0');unknown=0
    for doc in docs.order_by('-date','-pk'):
        sign=-1 if doc.kind=='return' else 1
        doc.margin=None
        if None in [doc.sale_amount,doc.total_cost,doc.selling_costs]:unknown+=1
        else:doc.margin=sign*(doc.sale_amount-doc.total_cost-doc.selling_costs)
        if doc.sale_amount is not None:revenue+=sign*doc.sale_amount
        if doc.total_cost is not None:cost+=sign*doc.total_cost
        if doc.selling_costs is not None:expenses+=sign*doc.selling_costs
        rows.append(doc)
    query=request.GET.copy();query.pop('page',None)
    if request.GET.get('export')=='xlsx':return xlsx_response('operating-report',['Документ','Дата','Заказ','Товар','Операция','Сумма','FIFO','Расходы','Результат'],([d.pk,d.date,d.external_number,str(d.item),d.get_kind_display(),d.sale_amount,d.total_cost,d.selling_costs,d.margin] for d in rows))
    return render(request,'inventory/operating_report.html',{'form':form,'page':Paginator(rows,50).get_page(request.GET.get('page')),'revenue':revenue,'cost':cost,'expenses':expenses,'unknown':unknown,'margin':None if unknown or not rows else revenue-cost-expenses,'query':query.urlencode()})


@never_cache
@accounting_access
def financial_details(request,pk):
    from .forms import FinancialDetailsForm
    with transaction.atomic():
        doc=get_object_or_404(StockDocument.objects.select_for_update(),pk=pk,kind__in=['sale','return'],status='posted')
        previous=f'Сумма: {doc.sale_amount}; расходы: {doc.selling_costs}'
        form=FinancialDetailsForm(request.POST or None,instance=doc)
        if request.method=='POST' and form.is_valid():
            doc=form.save(commit=False)
            doc.note+=f'\n{timezone.now().isoformat()} · {request.user}: {previous} → сумма {doc.sale_amount}; расходы {doc.selling_costs}. {form.cleaned_data["reason"]}'
            doc.save(update_fields=['sale_amount','selling_costs','note'])
            StockEvent.objects.create(document=doc,action='Уточнены суммы продажи',quantity=doc.quantity,user=request.user)
            messages.success(request,'Финансовые суммы уточнены. Количество и FIFO сохранены.');return redirect('inventory:operating_report')
    return render(request,'inventory/form.html',{'form':form,'title':f'Уточнить суммы документа №{pk}'})


@never_cache
@accounting_access
def bank_check(request):
    from .forms import BankCheckForm
    form=BankCheckForm(request.GET or None);expected=difference=None
    if form.is_bound and form.is_valid():
        d=form.cleaned_data;expected=d['account'].opening_balance
        for entry in CashEntry.objects.filter(account=d['account'],date__lte=d['date'],cancelled=False):expected+=entry.signed_amount
        difference=d['actual']-expected
    return render(request,'inventory/bank_check.html',{'form':form,'expected':expected,'difference':difference})
