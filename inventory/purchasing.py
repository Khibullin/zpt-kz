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
    payments=list(CashEntry.objects.filter(Q(purchase_order=order)|Q(stock_document__purchase_order=order),cancelled=False).distinct())
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
    rows=PurchaseOrder.objects.filter(user=request.user,archived=request.GET.get('deleted')=='1').select_related('supplier').order_by('-pk')
    q=request.GET.get('q','').strip()
    if q:
        query=Q(title__icontains=q)|Q(supplier__name__icontains=q)|Q(comment__icontains=q)|Q(supplier_order_number__icontains=q)
        number=q.upper().replace('ЗК-','')
        if number.isdigit():query |= Q(pk=int(number))
        rows=rows.filter(query)
    if request.GET.get('warehouse') in ['PP1','PP2']:rows=rows.filter(destination=request.GET['warehouse'])
    if request.GET.get('supplier','').isdigit():rows=rows.filter(supplier_id=request.GET['supplier'])
    results=[]
    for order in rows:
        order.workflow=order_summary(order)
        if request.GET.get('stage')=='expected' and (not order.workflow['remaining'] or order.delivery_stage in ['draft','cancelled'] or order.status=='cancelled'):continue
        if request.GET.get('stage')=='partial' and not (order.workflow['received'] and order.workflow['remaining']):continue
        if request.GET.get('stage')=='received' and not (order.workflow['received'] and not order.workflow['remaining']):continue
        if request.GET.get('payment')=='unpaid' and order.workflow['paid']>0:continue
        if request.GET.get('payment')=='paid' and order.workflow['payment_state'] not in ['Оплачено','Переплата']:continue
        results.append(order)
    if request.GET.get('export')=='xlsx':
        from .tools_views import xlsx_response
        return xlsx_response('purchase-register',['Номер','Закупка','Поставщик','Склад','План доставки','Поставка','Оплата','Позиций','Заказано','Принято','Ожидается','Сумма заказа','Оплачено','Сальдо по фактическим приходам'],([o.order_number,o.display_title,o.supplier.name,o.destination,o.expected_date,o.workflow['delivery_state'],o.workflow['payment_state'],o.workflow['positions'],o.workflow['ordered'],o.workflow['received'],o.workflow['remaining'],o.total,o.workflow['paid'],o.workflow['balance']] for o in results))
    from django.core.paginator import Paginator
    from procurement.models import Supplier
    total=sum((o.total for o in results),Decimal(0))
    query=request.GET.copy();query.pop('page',None)
    return render(request,'inventory/purchases.html',{'page':Paginator(results,50).get_page(request.GET.get('page')),'query':query.urlencode(),'count':len(results),'total':total,'suppliers':Supplier.objects.all()})


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
