"""Explicit, atomic purchase acceptance. GET never changes stock."""
import uuid
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .permissions import accounting_access
from procurement.models import PurchaseOrder, Activity, Supplier
from .models import Item, SupplierItem, StockDocument
from .services import post_document


class ReceiveForm(forms.Form):
    warehouse = forms.ChoiceField(label='Склад приёмки', choices=[('PP2','PP2 · Rapida'),('PP1','PP1 · Asia Grade')], initial='PP2')
    date = forms.DateField(label='Дата фактической приёмки', initial=timezone.localdate, widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    waybill = forms.CharField(label='Номер накладной', max_length=100, required=False)
    version = forms.IntegerField(widget=forms.HiddenInput)
    key = forms.UUIDField(widget=forms.HiddenInput)


class ReceiveLine(forms.Form):
    quantity = forms.IntegerField(label='Принято сейчас', min_value=0, max_value=100000)
    cost = forms.DecimalField(label='Закупочная цена за единицу, ₸', min_value=0, max_digits=14, decimal_places=2)
    unit = forms.ChoiceField(label='Единица учёта', choices=[('','Выберите'),('piece','Штука'),('set','Комплект')], required=False)


ReceiveLines = formset_factory(ReceiveLine, extra=0, max_num=20000, absolute_max=20000, validate_max=True)


def received_quantities(order):
    return {r['purchase_line']:r['total'] for r in order.stock_receipts.filter(status='posted',kind='receipt').values('purchase_line').annotate(total=Sum('quantity'))}


@transaction.atomic
def accept_purchase(pk, user, header, lines):
    order = PurchaseOrder.objects.select_for_update().select_related('supplier').get(pk=pk, user=user)
    Supplier.objects.select_for_update().get(pk=order.supplier_id)
    if order.stock_receipts.filter(receipt_key=header['key']).exists():
        return False
    if order.version != header['version']:
        raise ValidationError('Заказ изменился. Откройте приёмку заново.')
    if order.status == 'cancelled' or order.archived or order.delivery_stage=='cancelled':
        raise ValidationError('Отменённый заказ нельзя принять.')
    if len(lines) != len(order.items):
        raise ValidationError('Состав заказа изменился. Обновите страницу.')
    if header['date'] > timezone.localdate():
        raise ValidationError('Дата приёмки не может быть в будущем.')
    received = received_quantities(order)
    if not any(line['quantity'] for line in lines):
        raise ValidationError('Укажите фактически принятое количество хотя бы в одной строке.')
    for index, (source, line) in enumerate(zip(order.items, lines)):
        qty = line['quantity']
        if qty > source['quantity'] - received.get(index,0):
            raise ValidationError(f"{source['article']}: количество превышает непринятый остаток заказа.")
        if not qty:
            continue
        if line['unit'] not in ('piece','set'):
            raise ValidationError(f"{source['article']}: подтвердите штуку или комплект.")
        if not source.get('article'):
            raise ValidationError('У товара отсутствует артикул. Сначала исправьте заказ.')
        link = SupplierItem.objects.select_for_update().filter(supplier=order.supplier,article=source['article'],brand=source['brand']).first()
        if link:
            item = Item.objects.select_for_update().get(pk=link.item_id)
            if item.unit != line['unit']:
                raise ValidationError(f"{source['article']}: единица отличается от существующей карточки товара.")
        else:
            # Stable identity includes supplier, article and brand; no fuzzy merges.
            import hashlib
            digest=hashlib.sha256((source['brand']+'\0'+source['article']).encode()).hexdigest()[:24]
            item=Item.objects.create(code=f'SUP-{order.supplier_id}-{digest}',article=source['article'],brand=source['brand'],name=source['name'][:300],unit=line['unit'])
            SupplierItem.objects.create(supplier=order.supplier,article=source['article'],brand=source['brand'],item=item)
        doc=StockDocument.objects.create(kind='receipt',item=item,warehouse=header['warehouse'],quantity=qty,unit_cost=line['cost'],counterparty=order.supplier.name,external_number=order.order_number,date=header['date'],created_by=user,purchase_order=order,purchase_line=index,receipt_key=header['key'],note=f"Закуплено у {order.supplier.name}. {order.order_number}. Накладная: {header['waybill'] or order.waybill_number or 'не указана'}")
        post_document(doc.pk,user)
    now=received_quantities(order)
    if all(now.get(i,0) >= row['quantity'] for i,row in enumerate(order.items)):
        order.status='received'
    if header['waybill']:
        order.waybill_number=header['waybill']
    order.version+=1
    order.save(update_fields=['status','waybill_number','version','updated_at'])
    Activity.objects.create(user=user,action='purchase_stock_received',details={'purchase':order.pk,'warehouse':header['warehouse'],'receipt_key':str(header['key'])})
    return True


@accounting_access
def receive_purchase(request,pk):
    order=get_object_or_404(PurchaseOrder.objects.select_related('supplier'),pk=pk,user=request.user)
    received=received_quantities(order)
    links={(s.article,s.brand):s.item for s in SupplierItem.objects.filter(supplier=order.supplier).select_related('item')}
    initial=[]
    for index,source in enumerate(order.items):
        item=links.get((source['article'],source['brand']))
        initial.append({'quantity':max(0,source['quantity']-received.get(index,0)),'cost':source['cost'],'unit':item.unit if item else ''})
    form=ReceiveForm(request.POST or None,initial={'warehouse':order.destination,'date':timezone.localdate(),'waybill':order.waybill_number,'version':order.version,'key':uuid.uuid4()})
    lines=ReceiveLines(request.POST or None,prefix='lines',initial=initial)
    if request.method=='POST' and form.is_valid() and lines.is_valid():
        try:
            created=accept_purchase(pk,request.user,form.cleaned_data,lines.cleaned_data)
        except ValidationError as exc:
            form.add_error(None,exc)
        else:
            messages.success(request,'Приход проведён. Остатки склада и задолженность поставщику обновлены.' if created else 'Эта приёмка уже сохранена. Повторного прихода нет.')
            return redirect('procurement:purchase_detail',pk=pk)
    rows=[{'source':s,'received':received.get(i,0),'remaining':max(0,s['quantity']-received.get(i,0)),'form':f} for i,(s,f) in enumerate(zip(order.items,lines.forms))]
    return render(request,'inventory/purchase_receive.html',{'order':order,'form':form,'lines':lines,'rows':rows},status=400 if request.method=='POST' else 200)
