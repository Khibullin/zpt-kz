"""Document headers above the existing posting/FIFO ledger. No stock on GET."""
import hashlib
import unicodedata
from decimal import Decimal
from pathlib import PurePath
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import GoodsDocument, StockDocument, DocumentAttachment, Item
from .services import post_document, receive_transfer


def document_identity(kind, number, date, counterparty, warehouse, destination=''):
    if not number.strip():
        return None
    parts = [kind, number, str(date), counterparty, warehouse, destination]
    normal = '\0'.join(' '.join(unicodedata.normalize('NFKC', str(s)).casefold().split()) for s in parts)
    return hashlib.sha256(normal.encode()).hexdigest()


def existing_document(kind,number,date,counterparty,warehouse,destination='',exclude_pk=None):
    """An invoice's issue/acceptance date mix-up must not create a second receipt."""
    identity=document_identity(kind,number,date,counterparty,warehouse,destination)
    if not identity:return None
    from django.db.models import Q
    exact=Q(identity=identity)
    if kind=='receipt':
        exact|=Q(kind=kind,number__iexact=number.strip(),counterparty__iexact=counterparty.strip(),warehouse=warehouse,date__year=date.year)
    return GoodsDocument.objects.filter(exact).exclude(pk=exclude_pk).first()


def attach(document, uploaded, user):
    if uploaded.size > 15 * 1024 * 1024:
        raise ValidationError('Размер вложения — не более 15 МБ.')
    name = PurePath(uploaded.name.replace('\\', '/')).name[:255]
    if name.rsplit('.', 1)[-1].lower() not in ['pdf', 'png', 'jpg', 'jpeg', 'xlsx', 'xls', 'csv']:
        raise ValidationError('Прикрепите PDF, фото, XLSX, XLS или CSV.')
    data = uploaded.read()
    return DocumentAttachment.objects.get_or_create(document=document, digest=hashlib.sha256(data).hexdigest(), defaults={'name': name, 'data': data, 'created_by': user})[0]


def describe(bundle):
    if bundle.evidence_only:
        bundle.state='Архивный документ'
        bundle.position_count=len(bundle.source_lines)
        bundle.quantity_groups={}
        bundle.accepted_groups={}
        bundle.difference_count=0
        for row in bundle.source_lines:
            unit=row.get('unit','По исходному документу')
            bundle.quantity_groups[unit]=bundle.quantity_groups.get(unit,0)+row.get('planned',0)
            row['difference']=None if row.get('accepted') is None else row['accepted']-row['planned']
            if row.get('accepted') is not None:
                bundle.accepted_groups[unit]=bundle.accepted_groups.get(unit,0)+row['accepted']
                if row['difference']:bundle.difference_count+=1
        bundle.acceptance_count=0
        bundle.amount=None
        bundle.accepted_amount=Decimal(0)
        bundle.unknown_cost=True
        bundle.can_post=bundle.can_receive=False
        return bundle
    lines = list(bundle.lines.all())
    active = [d for d in lines if d.status != 'cancelled']
    states = {d.status for d in active}
    if not active:
        bundle.state = 'Отменён' if lines else 'Нет строк'
    elif states == {'draft'}:
        bundle.state = 'Черновик'
    elif states == {'posted'}:
        bundle.state = 'Принято полностью' if bundle.kind == 'receipt' else 'Проведён'
    elif states == {'received'}:
        bundle.state = 'Принято полностью'
    elif states <= {'sent', 'partial', 'received'}:
        bundle.state = 'Принято частично' if any(d.received_quantity for d in active) else 'В пути'
    else:
        bundle.state = 'Проведено частично'
    bundle.position_count = len({d.item_id for d in active})
    bundle.acceptance_count = len({str(d.receipt_key) if d.receipt_key else 'line-' + str(d.pk) for d in active if d.status == 'posted'}) if bundle.kind == 'receipt' else 0
    bundle.quantity_groups = {}
    bundle.amount = Decimal(0)
    bundle.unknown_cost = False
    bundle.accepted_amount = Decimal(0)
    for d in active:
        unit = d.item.get_unit_display()
        bundle.quantity_groups[unit] = bundle.quantity_groups.get(unit, 0) + d.quantity
        value = d.sale_amount if d.kind in ['sale','return'] else d.quantity * d.unit_cost if d.unit_cost is not None else None
        if value is None:
            bundle.unknown_cost = True
        else:
            bundle.amount += value
            if d.status == 'posted':
                bundle.accepted_amount += d.quantity * d.unit_cost
    if bundle.unknown_cost:
        bundle.amount = None
    bundle.can_post = bool(active) and states == {'draft'}
    bundle.can_receive = bundle.kind == 'transfer' and bool(states & {'sent', 'partial'})
    return bundle


@transaction.atomic
def post_bundle(pk, user):
    bundle = GoodsDocument.objects.select_for_update().get(pk=pk)
    if bundle.evidence_only:
        raise ValidationError('Архивный документ не проводится повторно. Он сохранён как основание исторической операции.')
    lines = list(bundle.lines.exclude(status='cancelled').select_related('item').order_by('item_id', 'pk'))
    if not lines or any(d.status != 'draft' for d in lines):
        raise ValidationError('Документ уже проведён, отменён или не содержит товаров.')
    # All item locks in a stable order. Entire multi-line document succeeds or rolls back.
    list(Item.objects.select_for_update().filter(pk__in=[d.item_id for d in lines]).order_by('pk'))
    for line in lines:
        post_document(line.pk, user)


@transaction.atomic
def receive_bundle(pk, quantities, user):
    bundle = GoodsDocument.objects.select_for_update().get(pk=pk, kind='transfer')
    lines = list(bundle.lines.select_related('item').order_by('item_id', 'pk'))
    list(Item.objects.select_for_update().filter(pk__in=[d.item_id for d in lines]).order_by('pk'))
    changed = False
    for line in lines:
        raw = str(quantities.get(str(line.pk), '0'))
        if not raw.isdigit() or len(raw) > 9:
            raise ValidationError('Количество приёмки должно быть целым и неотрицательным.')
        quantity = int(raw)
        if quantity:
            receive_transfer(line.pk, quantity, user)
            changed = True
    if not changed:
        raise ValidationError('Укажите принятое количество хотя бы одного товара.')
