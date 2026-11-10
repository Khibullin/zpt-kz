from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Max
from django.utils import timezone
from .costing import available_lots, take, slice_allocations, total_cost
from .models import Item, StockDocument, StockEntry, StockEvent

def balance(item, warehouse):
    return StockEntry.objects.filter(document__item=item,warehouse=warehouse).aggregate(q=Sum('quantity'))['q'] or 0

@transaction.atomic
def post_document(pk, user):
    doc = StockDocument.objects.select_for_update().get(pk=pk)
    # Serialize all stock changes of one item, including concurrent documents.
    Item.objects.select_for_update().get(pk=doc.item_id)
    if doc.item.unit == 'pending':
        raise ValidationError('Сначала подтвердите единицу учёта товара: штука или комплект.')
    if doc.status != 'draft':
        raise ValidationError('Документ уже проведён. Повторное проведение запрещено.')
    if doc.quantity < 1:
        raise ValidationError('Количество должно быть положительным.')
    if doc.unit_cost is not None and doc.unit_cost < 0:
        raise ValidationError('Себестоимость не может быть отрицательной.')
    latest = StockEntry.objects.filter(document__item=doc.item).aggregate(latest=Max('effective_on'))['latest']
    if latest and doc.date < latest:
        raise ValidationError('Дата раньше уже проведённых движений этого товара. Заднее число требует отдельной сверки.')
    if doc.kind not in StockDocument.Kind.values:raise ValidationError('Неизвестная операция')
    if doc.kind in ['adjustment','writeoff'] and not doc.note.strip():raise ValidationError('Укажите причину инвентаризационной корректировки.')
    if doc.kind == 'supplier_return':
        receipt=doc.related_receipt
        if not receipt or receipt.kind!='receipt' or receipt.status!='posted' or receipt.item_id!=doc.item_id or not doc.note.strip():
            raise ValidationError('Для возврата поставщику укажите исходный приход и причину.')
        lots=[lot for lot in available_lots(doc.item)[doc.warehouse] if lot['origin']==receipt.pk]
        doc.cost_allocations=take(lots,doc.quantity)
        doc.total_cost=total_cost(doc.cost_allocations)
        StockEntry.objects.create(document=doc,warehouse=doc.warehouse,quantity=-doc.quantity,created_by=user)
        doc.status='posted'
    elif doc.kind == 'writeoff':
        doc.cost_allocations=take(available_lots(doc.item)[doc.warehouse],doc.quantity)
        doc.total_cost=total_cost(doc.cost_allocations)
        StockEntry.objects.create(document=doc,warehouse=doc.warehouse,quantity=-doc.quantity,created_by=user)
        doc.status='posted'
    elif doc.kind == 'sale':
        if doc.warehouse != 'PP2' or not doc.external_number:
            raise ValidationError('Для отгрузки укажите PP2 и номер заказа Kaspi.')
        if StockDocument.objects.filter(kind='sale',item=doc.item,external_number=doc.external_number).exclude(pk=doc.pk).exists():
            raise ValidationError('Отгрузка этого товара по заказу уже существует.')
        doc.cost_allocations = take(available_lots(doc.item)['PP2'],doc.quantity)
        doc.total_cost = total_cost(doc.cost_allocations)
        StockEntry.objects.create(document=doc,warehouse='PP2',quantity=-doc.quantity,created_by=user)
        doc.status = 'posted'
    elif doc.kind == 'return':
        if not doc.related_sale_id or doc.return_condition not in ['good','damaged']:
            raise ValidationError('Укажите исходную отгрузку и результат проверки возврата.')
        sale = StockDocument.objects.get(pk=doc.related_sale_id)
        if sale.kind != 'sale' or sale.status != 'posted' or sale.item_id != doc.item_id or doc.warehouse != 'PP2':
            raise ValidationError('Возврат должен соответствовать проведённой отгрузке того же товара на PP2.')
        returned = sale.returns.filter(status='posted').aggregate(q=Sum('quantity'))['q'] or 0
        if returned + doc.quantity > sale.quantity:
            raise ValidationError('Возврат превышает количество исходной отгрузки.')
        doc.cost_allocations = slice_allocations(sale.cost_allocations,returned,doc.quantity)
        doc.total_cost = total_cost(doc.cost_allocations)
        StockEntry.objects.create(document=doc,warehouse='PP2' if doc.return_condition == 'good' else 'DAMAGED',quantity=doc.quantity,created_by=user)
        doc.status = 'posted'
    elif doc.kind == 'transfer':
        if doc.warehouse == doc.destination or doc.destination not in ['PP1','PP2']:
            raise ValidationError('Выберите другой склад получения.')
        if balance(doc.item,doc.warehouse) < doc.quantity:
            raise ValidationError('Недостаточно проведённого остатка на складе отправления. Сначала подтвердите начальный остаток.')
        StockEntry.objects.create(document=doc,warehouse=doc.warehouse,quantity=-doc.quantity,created_by=user)
        StockEntry.objects.create(document=doc,warehouse='TRANSIT',quantity=doc.quantity,created_by=user)
        doc.status = 'sent'
    else:
        if doc.kind == 'receipt' and (doc.unit_cost is None or not doc.counterparty):
            raise ValidationError('Для прихода укажите поставщика и себестоимость единицы.')
        if doc.kind == 'opening' and StockEntry.objects.filter(document__item=doc.item,warehouse=doc.warehouse).exists():
            raise ValidationError('Начальный остаток по этому товару и складу уже установлен или были движения.')
        StockEntry.objects.create(document=doc,warehouse=doc.warehouse,quantity=doc.quantity,created_by=user)
        doc.status = 'posted'
    doc.save(update_fields=['status','cost_allocations','total_cost'])
    StockEvent.objects.create(document=doc,action=doc.status,quantity=doc.quantity,user=user)
    return doc

@transaction.atomic
def receive_transfer(pk, quantity, user):
    doc = StockDocument.objects.select_for_update().get(pk=pk)
    Item.objects.select_for_update().get(pk=doc.item_id)
    if doc.kind != 'transfer' or doc.status not in ['sent','partial']:
        raise ValidationError('Перемещение недоступно для приёмки.')
    if quantity < 1 or quantity > doc.quantity-doc.received_quantity:
        raise ValidationError('Количество превышает непринятый остаток или равно нулю.')
    StockEntry.objects.create(document=doc,warehouse='TRANSIT',quantity=-quantity,created_by=user,effective_on=timezone.localdate())
    StockEntry.objects.create(document=doc,warehouse=doc.destination,quantity=quantity,created_by=user,effective_on=timezone.localdate())
    doc.received_quantity += quantity
    doc.status = 'received' if doc.received_quantity == doc.quantity else 'partial'
    doc.save(update_fields=['status','received_quantity'])
    StockEvent.objects.create(document=doc,action=doc.status,quantity=quantity,user=user)
    return doc
