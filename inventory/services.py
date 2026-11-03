from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from .models import Item, StockDocument, StockEntry, StockEvent

def balance(item, warehouse):
    return StockEntry.objects.filter(document__item=item,warehouse=warehouse).aggregate(q=Sum('quantity'))['q'] or 0

@transaction.atomic
def post_document(pk, user):
    doc = StockDocument.objects.select_for_update().get(pk=pk)
    # Serialize all stock changes of one item, including concurrent documents.
    Item.objects.select_for_update().get(pk=doc.item_id)
    if doc.status != 'draft':
        raise ValidationError('Документ уже проведён. Повторное проведение запрещено.')
    if doc.quantity < 1:
        raise ValidationError('Количество должно быть положительным.')
    if doc.unit_cost is not None and doc.unit_cost < 0:
        raise ValidationError('Себестоимость не может быть отрицательной.')
    if doc.kind == 'transfer':
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
    doc.save(update_fields=['status'])
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
    StockEntry.objects.create(document=doc,warehouse='TRANSIT',quantity=-quantity,created_by=user)
    StockEntry.objects.create(document=doc,warehouse=doc.destination,quantity=quantity,created_by=user)
    doc.received_quantity += quantity
    doc.status = 'received' if doc.received_quantity == doc.quantity else 'partial'
    doc.save(update_fields=['status','received_quantity'])
    StockEvent.objects.create(document=doc,action=doc.status,quantity=quantity,user=user)
    return doc
