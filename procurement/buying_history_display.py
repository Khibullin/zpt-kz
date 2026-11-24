"""Read-only purchase and physical stock information for profitable buying."""
from collections import defaultdict
from django.db.models import Sum, Q
from inventory.permissions import can_access_accounting
from inventory.models import Item, SupplierItem, StockEntry
from .models import PurchaseOrder


def product_key(brand, article):
    return (str(brand or '').strip().casefold(), str(article or '').strip().casefold())


def buying_history(user):
    orders = defaultdict(list)
    choices = []
    queryset = PurchaseOrder.objects.filter(archived=False).exclude(
        status='cancelled').exclude(delivery_stage='cancelled').exclude(
        Q(status='draft') & Q(delivery_stage='draft'))
    if not (user.is_superuser and can_access_accounting(user)):
        queryset = queryset.filter(user=user)
    for order in queryset.select_related('supplier').order_by('created_at', 'pk'):
        label = order.supplier_order_number or order.order_number
        if order.supplier.name == 'Omega' and order.pk == 2:
            label = 'Заказ №1'
        choices.append({'value': f'order:{order.pk}', 'label': f'{label} — {order.supplier.name}'})
        quantities = defaultdict(int)
        for line in order.items:
            key = product_key(line.get('brand'), line.get('article'))
            if all(key):
                quantities[key] += int(line.get('quantity') or 0)
        for key, quantity in quantities.items():
            if quantity > 0:
                orders[key].append({'pk': order.pk, 'quantity': quantity, 'label': label, 'own': order.user_id == user.pk})
    links = defaultdict(set)
    for brand, article, item_id in SupplierItem.objects.values_list('brand', 'article', 'item_id'):
        key = product_key(brand, article)
        if all(key):
            links[key].add(item_id)
    for brand, article, item_id in Item.objects.exclude(brand='').exclude(article='').values_list('brand', 'article', 'pk'):
        key = product_key(brand, article)
        if all(key):
            links[key].add(item_id)
    balances = dict(StockEntry.objects.filter(warehouse__in=['PP1', 'PP2']).values(
        'document__item_id').annotate(total=Sum('quantity')).values_list('document__item_id', 'total'))
    stock = {key: sum(balances.get(item_id, 0) for item_id in item_ids)
             for key, item_ids in links.items() if any(item_id in balances for item_id in item_ids)}
    return orders, stock, choices
