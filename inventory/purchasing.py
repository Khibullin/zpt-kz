"""One purchasing record shared by procurement and accounting."""
from decimal import Decimal
from urllib.parse import urlparse
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from procurement.models import PurchaseOrder, Activity
from .permissions import accounting_access
from .models import CashEntry, MoneyAccount, StockDocument, SupplierItem, PurchaseListing, StockEntry
from .purchase_receiving import received_quantities
from .services import post_document


def order_summary(order):
    received=received_quantities(order)
    returns=order.stock_receipts.filter(kind='supplier_return',status='posted')
    returned={r['purchase_line']:r['q'] for r in returns.values('purchase_line').annotate(q=Sum('quantity'))}
    payments=list(CashEntry.objects.filter(Q(purchase_order=order)|Q(stock_document__purchase_order=order)|Q(goods_document__purchase_order=order),cancelled=False).distinct())
    paid=sum((e.amount if e.kind=='supplier' else -e.amount if e.kind=='supplier_refund' else Decimal(0) for e in payments),Decimal(0))
    value=sum((d.quantity*d.unit_cost for d in order.stock_receipts.filter(kind='receipt',status='posted')),Decimal(0))-sum((d.total_cost or Decimal(0) for d in returns),Decimal(0))
    ordered=sum(i['quantity'] for i in order.items)
    accepted=sum(received.values())
    inactive=order.archived or order.delivery_stage=='cancelled' or order.status=='cancelled'
    remaining=0 if inactive else max(0,ordered-accepted)
    states={s.line:s for s in order.listing_states.all()}
    lines=[]
    for idx,source in enumerate(order.items):
        lines.append({'index':idx,'source':source,'received':received.get(idx,0),'returned':returned.get(idx,0),'remaining':0 if inactive else max(0,source['quantity']-received.get(idx,0)),'listing':states.get(idx)})
    payment_target=value if inactive or accepted and not remaining else order.total
    payment='Не оплачено' if paid==0 else 'Возврат превышает оплату' if paid<0 else 'Частично оплачено' if paid<payment_target else 'Оплачено' if paid==payment_target else 'Переплата'
    delivery='Удалён' if order.archived else 'Отменён' if order.delivery_stage=='cancelled' or order.status=='cancelled' else 'Принято полностью' if accepted and not remaining else 'Принято частично' if accepted else order.get_delivery_stage_display()
    return {'lines':lines,'ordered':ordered,'received':accepted,'returned':sum(returned.values()),'remaining':remaining,'paid':paid,'receipt_value':value,'balance':value-paid,'to_pay':max(Decimal(0),payment_target-paid),'payment_target':payment_target,'payment_state':payment,'delivery_state':delivery,'payments':payments,'positions':sum(i['quantity']>0 for i in order.items),'overdue':bool(order.expected_date and order.expected_date<timezone.localdate() and remaining and order.delivery_stage!='draft')}


class PlanningForm(forms.ModelForm):
    reason=forms.CharField(label='Причина отмены (при отмене)',required=False,max_length=1000)
    def clean(self):
        d=super().clean()
        if d.get('delivery_stage')=='cancelled' and not d.get('reason'):self.add_error('reason','Укажите причину отмены.')
        return d
    version=forms.IntegerField(widget=forms.HiddenInput)
    class Meta:
        model=PurchaseOrder
        fields=['title','expected_date','destination','delivery_stage']
        widgets={'expected_date':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'})}


class PaymentForm(forms.Form):
    account=forms.ModelChoiceField(label='Счёт / касса',queryset=MoneyAccount.objects.all(),help_text='Если счёт ещё не создан: Деньги → Добавить счёт. Здесь фиксируется уже совершённая операция; банковский перевод не выполняется.')
    date=forms.DateField(label='Дата фактической операции',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    kind=forms.ChoiceField(label='Операция',choices=[('supplier','Оплата / аванс поставщику'),('supplier_refund','Возврат денег от поставщика')])
    amount=forms.DecimalField(label='Сумма, ₸',min_value=Decimal('0.01'),max_digits=14,decimal_places=2)
    reference=forms.CharField(label='Номер платежа / основание',max_length=150)
    note=forms.CharField(label='Комментарий',required=False,widget=forms.Textarea)
    def clean(self):
        d=super().clean()
        if d.get('date') and (d['date']>timezone.localdate() or d.get('account') and d['date']<d['account'].opening_date):
            self.add_error('date','Дата должна быть не раньше начала учёта счёта и не позже сегодняшнего дня.')
        return d


class ListingForm(forms.ModelForm):
    class Meta:
        model=PurchaseListing
        fields=['state','url','price','quantity','checked_on']
        labels={'state':'Состояние продажи','url':'Ссылка на карточку Kaspi','price':'Цена продажи, ₸','quantity':'Количество, выставленное на продажу','checked_on':'Дата проверки размещения'}
        widgets={'checked_on':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'})}
    def clean(self):
        d=super().clean()
        if d.get('state') in ['ready','listed'] and (not d.get('price') or d['price']<=0):self.add_error('price','Укажите положительную цену продажи.')
        if d.get('state')=='listed':
            host=urlparse(d.get('url','')).hostname or ''
            if host!='kaspi.kz' and not host.endswith('.kaspi.kz'):self.add_error('url','Укажите ссылку на карточку Kaspi.')
            if not d.get('checked_on') or d['checked_on']>timezone.localdate():self.add_error('checked_on','Укажите фактическую дату проверки размещения.')
            if not d.get('quantity'):self.add_error('quantity','Укажите выставленное количество.')
        return d


def locked_order(pk,user):
    return get_object_or_404(PurchaseOrder.objects.select_for_update(),pk=pk,user=user)


@never_cache
@accounting_access
def purchases(request):
    """Combined register of supplier orders and verified standalone receipts."""
    from collections import defaultdict
    from django.core.paginator import Paginator
    from procurement.models import Supplier
    from procurement.views import context as procurement_context
    from .models import GoodsDocument

    deleted=request.GET.get('deleted')=='1'
    query_text=request.GET.get('q','').strip()
    warehouse=request.GET.get('warehouse','')
    supplier_id=request.GET.get('supplier','')
    stage=request.GET.get('stage','')
    payment=request.GET.get('payment','')
    entries=[]
    orders=PurchaseOrder.objects.filter(archived=deleted).select_related('supplier').order_by('-created_at','-pk')
    if not request.user.is_superuser:
        orders=orders.filter(user=request.user)
    if warehouse in ('PP1','PP2'):
        orders=orders.filter(destination=warehouse)
    if supplier_id.isdigit():
        orders=orders.filter(supplier_id=int(supplier_id))
    for order in orders:
        if query_text:
            needle=query_text.casefold()
            text=' '.join([order.title or '',order.order_number,order.supplier.name,
                           order.supplier_order_number or '',order.comment or '']+
                          [str(line.get(key,'')) for line in (order.items or []) for key in ('name','article','brand')])
            if needle not in text.casefold():continue
        wf=order_summary(order)
        china_unconfirmed=('e03cf42556bc1d5ba57c9756bde19c44c664d96830bc4de465f2c2af24f1a325' in (order.comment or ''))
        if stage=='expected' and (not wf['remaining'] or order.delivery_stage in ('draft','cancelled') or order.status=='cancelled'):continue
        if stage=='partial' and not(wf['received'] and wf['remaining']):continue
        if stage=='received' and not(wf['received'] and not wf['remaining']):continue
        if payment=='unpaid' and wf['paid']>0:continue
        if payment=='paid' and wf['payment_state'] not in ('Оплачено','Переплата'):continue
        entries.append({'kind':'order','order':order,'number':order.order_number,
                        'title':order.display_title,'supplier':order.supplier.name,
                        'date':None if china_unconfirmed else order.created_at.date(),
                        'sort_date':order.created_at.date(),'positions':wf['positions'],
                        'quantity':wf['ordered'],'received':wf['received'],
                        'awaiting':0 if china_unconfirmed else wf['remaining'],'amount':order.total,
                        'status':'Поставка не подтверждена' if china_unconfirmed else wf['delivery_state'],'payment':wf['payment_state'],
                        'item_quantities':{},'receipt_quantity':0,'stock_known':False})

    receipts=[] if deleted else list(GoodsDocument.objects.filter(
        kind='receipt',purchase_order__isnull=True,evidence_only=False,
    ).prefetch_related('lines__item').order_by('-date','-pk'))
    supplier_name=Supplier.objects.filter(pk=int(supplier_id)).values_list('name',flat=True).first() if supplier_id.isdigit() else None
    for doc in receipts:
        if warehouse in ('PP1','PP2') and doc.warehouse!=warehouse:continue
        if supplier_id.isdigit() and (not supplier_name or doc.counterparty.casefold()!=supplier_name.casefold()):continue
        lines=[line for line in doc.lines.all() if line.kind=='receipt' and line.status=='posted']
        if not lines:continue
        if query_text:
            searchable=' '.join([doc.number or '',doc.counterparty or '',doc.note or '']+
                                [str(line.item) for line in lines])
            if query_text.casefold() not in searchable.casefold():continue
        if stage in ('expected','partial') or payment in ('unpaid','paid'):continue
        quantities=defaultdict(int)
        for line in lines:quantities[line.item_id]+=line.quantity
        qty=sum(quantities.values())
        priced=all(line.unit_cost is not None for line in lines)
        amount=sum((line.quantity*line.unit_cost for line in lines),Decimal(0)) if priced else None
        entries.append({'kind':'receipt','receipt':doc,'number':doc.number or f'Приход №{doc.pk}',
                        'title':'Закупка по накладной','supplier':doc.counterparty or 'Поставщик не указан',
                        'date':doc.date,'sort_date':doc.date,'positions':len(quantities),'quantity':qty,
                        'received':qty,'awaiting':0,'amount':amount,
                        'status':'Принято на склад','payment':'Оплата не связана',
                        'item_quantities':dict(quantities),'receipt_quantity':qty,'stock_known':True})

    # Only actual stock entries count toward available goods. Shipment/return
    # attribution to a particular purchase lot is not yet recorded.
    orders_by_id={e['order'].pk:e for e in entries if e['kind']=='order'}
    from .models import StockDocument, StockEntry
    if orders_by_id:
        for row in StockDocument.objects.filter(
            purchase_order_id__in=orders_by_id,kind='receipt',status='posted'
        ).values('purchase_order_id','item_id','quantity'):
            e=orders_by_id[row['purchase_order_id']]
            e['item_quantities'][row['item_id']]=e['item_quantities'].get(row['item_id'],0)+row['quantity']
            e['receipt_quantity']+=row['quantity']
            e['stock_known']=True
    # China source lines are not tied to receiving documents, but exact SKU
    # matches exist in the last imported PP1 / Rapida PP2 stock snapshots.
    # These snapshot balances must NEVER be treated as received lot quantities.
    china_source_hash='e03cf42556bc1d5ba57c9756bde19c44c664d96830bc4de465f2c2af24f1a325'
    from .models import ImportBatch, StockSnapshot
    china_entries=[e for e in entries if e['kind']=='order' and china_source_hash in (e['order'].comment or '')]
    snapshot_batches={kind:ImportBatch.objects.filter(kind=kind).order_by('-as_of','-pk').first() for kind in ('pp1','pp2')} if china_entries else {}
    if china_entries:
        snap_ids=[b.pk for b in snapshot_batches.values() if b]
        snapshot_rows=StockSnapshot.objects.filter(batch_id__in=snap_ids).values('batch__kind','sku','quantity')
        stock_by_sku={}
        for r in snapshot_rows:
            key=(r['batch__kind'],r['sku'].strip().upper())
            stock_by_sku[key]=r['quantity']
        for e in china_entries:
            pp1=pp2=0
            matched=0
            unavailable=[]
            for line in e['order'].items:
                article=str(line.get('article','')).strip().upper()
                a=stock_by_sku.get(('pp1',article))
                b=stock_by_sku.get(('pp2',article))
                if a is None and b is None:
                    unavailable.append(article)
                    continue
                matched+=1
                pp1+=max(0,a or 0)
                pp2+=max(0,b or 0)
            e['snapshot_stock']={
                'pp1':pp1,'pp2':pp2,'total':pp1+pp2,
                'matched':matched,'unmatched':unavailable,
                'pp1_date':snapshot_batches['pp1'].as_of if snapshot_batches['pp1'] else None,
                'pp2_date':snapshot_batches['pp2'].as_of if snapshot_batches['pp2'] else None,
            }
        item_ids={item_id for e in entries for item_id in e['item_quantities']}
    current_stock=dict(StockEntry.objects.filter(
        document__item_id__in=item_ids,warehouse__in=('PP1','PP2')
    ).values('document__item_id').annotate(q=Sum('quantity')).values_list('document__item_id','q')) if item_ids else {}
    for e in entries:
        e['stock_available']=sum(min(qty,max(0,current_stock.get(item_id,0) or 0))
                                 for item_id,qty in e['item_quantities'].items())
        e['stock_denominator']=e['receipt_quantity']
        e['stock_percent']=round(100*e['stock_available']/e['stock_denominator']) if e['stock_denominator'] else 0
    for e in china_entries:
        snapshot=e['snapshot_stock']
        e['stock_known']=True
        e['stock_available']=snapshot['total']
        e['stock_denominator']=e['quantity']
        e['stock_percent']=min(100,round(100*e['stock_available']/e['quantity'])) if e['quantity'] else 0
        e['stock_pp1']=snapshot['pp1']
        e['stock_pp2']=snapshot['pp2']
        e['stock_label']='По снимкам PP1 / PP2'
        e['matched_articles']=snapshot['matched']
        e['unmatched_articles']=len(snapshot['unmatched'])
        e['pp1_date']=snapshot['pp1_date']
        e['pp2_date']=snapshot['pp2_date']
        entries.sort(key=lambda e:(e['sort_date'],e['kind']=='order',e['number']),reverse=True)
    total=sum((e['amount'] or Decimal(0) for e in entries),Decimal(0))
    total_unpriced=sum(e['amount'] is None for e in entries)
    count=len(entries)
    ordered=sum(e['quantity'] for e in entries)
    accepted=sum(e['received'] for e in entries)
    awaiting=sum(e['awaiting'] for e in entries)
    if request.GET.get('export')=='xlsx':
        from .tools_views import xlsx_response
        return xlsx_response('purchase-register',
            ['Номер','Тип','Поставщик','Дата','Позиций','Закуплено','Принято','Ожидается','Доступно по артикулам (оценка)','Сумма','Статус'],
            ([e['number'],e['kind'],e['supplier'],e['date'],e['positions'],e['quantity'],
              e['received'],e['awaiting'],e['stock_available'] if e['stock_known'] else None,
              e['amount'],e['status']] for e in entries))
    query=request.GET.copy();query.pop('page',None)
    return render(request,'inventory/purchases.html',{**procurement_context(request),
        'page':Paginator(entries,25).get_page(request.GET.get('page')),
        'query':query.urlencode(),'count':count,'total':total,'unpriced_count':total_unpriced,
        'ordered':ordered,'received':accepted,'awaiting':awaiting,
        'suppliers':Supplier.objects.order_by('name'),'deleted':deleted})


@never_cache
@accounting_access
def purchase_register_detail(request, pk):
    """Read-only purchase detail for the shared procurement register."""
    from procurement.views import context as procurement_context
    order = get_object_or_404(PurchaseOrder.objects.select_related('supplier'), pk=pk)
    if not request.user.is_superuser and order.user_id != request.user.pk:
        from django.http import Http404
        raise Http404
    summary = order_summary(order)
    china_unconfirmed='e03cf42556bc1d5ba57c9756bde19c44c664d96830bc4de465f2c2af24f1a325' in (order.comment or '')
    pp1_batch=pp2_batch=None
    china_stock=None
    if china_unconfirmed:
        from .models import ImportBatch, StockSnapshot
        pp1_batch=ImportBatch.objects.filter(kind='pp1').order_by('-as_of','-pk').first()
        pp2_batch=ImportBatch.objects.filter(kind='pp2').order_by('-as_of','-pk').first()
        batch_ids=[b.pk for b in (pp1_batch,pp2_batch) if b]
        rows=StockSnapshot.objects.filter(batch_id__in=batch_ids).values('batch__kind','sku','quantity')
        snapshot={(r['batch__kind'],r['sku'].strip().upper()):r['quantity'] for r in rows}
        china_stock={'pp1':0,'pp2':0,'matched':0,'unmatched':[]}
        for line in summary['lines']:
            article=str(line['source'].get('article','')).strip().upper()
            pp1=snapshot.get(('pp1',article))
            pp2=snapshot.get(('pp2',article))
            line['pp1_stock']=pp1
            line['pp2_stock']=pp2
            if pp1 is not None or pp2 is not None:
                china_stock['matched']+=1
                china_stock['pp1']+=max(0,pp1 or 0)
                china_stock['pp2']+=max(0,pp2 or 0)
            else:
                china_stock['unmatched'].append(article)
    return render(request, 'inventory/purchase_register_detail.html', {
        **procurement_context(request), 'order': order, 'summary': summary,
        'is_china_unconfirmed': china_unconfirmed,
        'china_stock':china_stock,'pp1_batch':pp1_batch,'pp2_batch':pp2_batch,
        'attachments': order.documents.all(), 'receipts': order.goods_documents.all(),
    })


@never_cache
@accounting_access
def purchase_action(request,pk,action):
    order=get_object_or_404(PurchaseOrder.objects.select_related('supplier'),pk=pk,user=request.user)
    summary=order_summary(order)
    if action=='plan':
        form=PlanningForm(request.POST or None,instance=order,initial={'version':order.version})
        if request.method=='POST' and form.is_valid():
            with transaction.atomic():
                current=locked_order(pk,request.user)
                if current.archived or current.version!=form.cleaned_data['version']:form.add_error(None,'Заказ изменён или удалён. Откройте его заново.')
                elif form.cleaned_data['delivery_stage']=='cancelled' and current.stock_receipts.exists():form.add_error(None,'Есть приход. Используйте возврат поставщику; история приёмки сохраняется.')
                else:
                    for field in form.Meta.fields:setattr(current,field,form.cleaned_data[field])
                    if current.delivery_stage=='cancelled':current.status='cancelled'
                    elif current.status=='cancelled':current.status='confirmed'
                    current.version+=1;current.save()
                    Activity.objects.create(user=request.user,action='purchase_planned',details={'purchase':pk,'stage':current.delivery_stage,'date':str(current.expected_date),'warehouse':current.destination,'reason':form.cleaned_data.get('reason','')})
                    messages.success(request,'План поставки сохранён. Складской приход не создавался.')
                    return redirect('procurement:purchase_detail',pk=pk)
        title='Название и план поставки'
    elif action=='payment':
        form=PaymentForm(request.POST or None)
        if request.method=='POST' and form.is_valid():
            d=form.cleaned_data
            try:
                with transaction.atomic():
                    current=locked_order(pk,request.user)
                    if current.archived:raise ValidationError('Заказ удалён. Сначала восстановите его.')
                    if current.status=='cancelled' and d['kind']=='supplier':raise ValidationError('Заказ отменён. Доступна только регистрация возврата денег.')
                    if d['kind']=='supplier_refund' and d['amount']>order_summary(current)['paid']:raise ValidationError('Возврат превышает зарегистрированную оплату.')
                    CashEntry.objects.create(purchase_order=current,created_by=request.user,counterparty=current.supplier.name,category='Закупка / аванс' if d['kind']=='supplier' else 'Возврат от поставщика',**d)
                    current.version+=1;current.save(update_fields=['version','updated_at'])
                    Activity.objects.create(user=request.user,action='purchase_payment',details={'purchase':pk,'kind':d['kind'],'amount':str(d['amount'])})
                messages.success(request,'Денежная операция записана. Остаток товара не изменён.')
                return redirect('procurement:purchase_detail',pk=pk)
            except (ValidationError,IntegrityError) as exc:form.add_error(None,exc if isinstance(exc,ValidationError) else 'Номер платежа уже использован на этом счёте. Повторная операция не записана.')
        title='Записать оплату / возврат денег'
    elif action in ['delete','restore','remove']:
        form=None
        if request.method=='POST':
            with transaction.atomic():
                current=locked_order(pk,request.user)
                if request.POST.get('version')!=str(current.version):messages.error(request,'Заказ изменился. Повторите действие после обновления.');return redirect('procurement:purchase_detail',pk=pk)
                protected=current.stock_receipts.exists() or CashEntry.objects.filter(Q(purchase_order=current)|Q(stock_document__purchase_order=current)).exists()
                if action!='restore' and protected:messages.error(request,'Есть финансовые или складские операции. Используйте отмену, возврат товара или исправление денежной записи.');return redirect('procurement:purchase_detail',pk=pk)
                if action=='remove':
                    try:index=int(request.POST.get('line',''))
                    except ValueError:index=-1
                    if not 0<=index<len(current.items):messages.error(request,'Позиция не найдена.');return redirect('procurement:purchase_detail',pk=pk)
                    current.items[index]['quantity']=0
                    current.items[index]['subtotal']='0'
                else:current.archived=action=='delete'
                current.version+=1;current.save()
                Activity.objects.create(user=request.user,action='purchase_'+action,details={'purchase':pk,'line':request.POST.get('line')})
            messages.success(request,'Позиция удалена из действующего состава.' if action=='remove' else 'Заказ восстановлен.' if action=='restore' else 'Заказ удалён из действующего учёта. Доступен в разделе «Удалённые».')
            return redirect('inventory:purchases')
        selected=None
        if action=='remove':
            try:selected=order.items[int(request.GET.get('line',''))]
            except (ValueError,IndexError):
                from django.http import Http404
                raise Http404
        return render(request,'inventory/purchase_confirm_action.html',{'order':order,'action':action,'selected':selected,'line':request.GET.get('line'),'summary':summary})
    elif action=='return':
        class ReturnForm(forms.Form):
            receipt=forms.ModelChoiceField(label='Исходный приход',queryset=order.stock_receipts.filter(kind='receipt',status='posted'))
            warehouse=forms.ChoiceField(label='Со склада',choices=[('PP2','PP2'),('PP1','PP1')])
            quantity=forms.IntegerField(label='Количество возвращено',min_value=1,max_value=100000)
            date=forms.DateField(label='Дата возврата',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
            reason=forms.CharField(label='Причина / документ',widget=forms.Textarea)
            version=forms.IntegerField(widget=forms.HiddenInput)
        form=ReturnForm(request.POST or None,initial={'version':order.version,'warehouse':order.destination})
        if request.method=='POST' and form.is_valid():
            d=form.cleaned_data
            try:
                with transaction.atomic():
                    current=locked_order(pk,request.user)
                    if current.version!=d['version'] or current.archived:raise ValidationError('Заказ изменён. Обновите страницу.')
                    if d['date']>timezone.localdate():raise ValidationError('Дата возврата не может быть в будущем.')
                    source=d['receipt']
                    doc=StockDocument.objects.create(kind='supplier_return',item=source.item,related_receipt=source,purchase_order=current,purchase_line=source.purchase_line,warehouse=d['warehouse'],quantity=d['quantity'],unit_cost=source.unit_cost,counterparty=current.supplier.name,external_number=current.order_number,date=d['date'],note=d['reason'],created_by=request.user)
                    post_document(doc.pk,request.user)
                    Activity.objects.create(user=request.user,action='purchase_supplier_return',details={'purchase':pk,'quantity':d['quantity'],'reason':d['reason'],'receipt':source.pk})
                    current.version+=1;current.save(update_fields=['version','updated_at'])
                messages.success(request,'Возврат поставщику проведён. Денежный возврат регистрируется отдельно.')
                return redirect('procurement:purchase_detail',pk=pk)
            except ValidationError as exc:form.add_error(None,exc)
        title='Возврат товара поставщику'
    else:
        from django.http import Http404
        raise Http404
    return render(request,'inventory/form.html',{'form':form,'title':f'{title} · {order.order_number} · {order.supplier.name}'},status=400 if request.method=='POST' else 200)


@never_cache
@accounting_access
def listing(request,pk,line):
    order=get_object_or_404(PurchaseOrder,pk=pk,user=request.user,archived=False)
    if not 0<=line<len(order.items):
        from django.http import Http404
        raise Http404
    instance=PurchaseListing.objects.filter(order=order,line=line).first()
    form=ListingForm(request.POST or None,instance=instance)
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data;source=order.items[line]
        with transaction.atomic():
            current=locked_order(pk,request.user)
            link=SupplierItem.objects.filter(supplier=current.supplier,article=source['article'],brand=source['brand']).first()
            stock=StockEntry.objects.filter(document__item=link.item,warehouse='PP2').aggregate(q=Sum('quantity'))['q'] if link else None
            accepted=received_quantities(current).get(line,0)
            if current.archived or current.status=='cancelled':form.add_error(None,'Заказ удалён или отменён.')
            elif d['state'] in ['ready','listed'] and (not accepted or not stock or d['quantity']>stock):form.add_error(None,'Нужно принять товар на PP2; количество не должно превышать проведённый остаток.')
            else:
                obj=form.save(commit=False);obj.order=current;obj.line=line;obj.updated_by=request.user;obj.save()
                Activity.objects.create(user=request.user,action='purchase_listing',details={'purchase':pk,'line':line,'state':obj.state,'quantity':obj.quantity,'url':obj.url})
                messages.success(request,'Состояние продажи записано по вашей проверке. Это действие не отправляет товары в Kaspi.')
                return redirect('procurement:purchase_detail',pk=pk)
    return render(request,'inventory/form.html',{'form':form,'title':'Подготовка / размещение на Kaspi · '+order.items[line]['name']},status=400 if request.method=='POST' else 200)
