"""Connect the already-posted Omega receipt to its exact historical order.

No receipt, stock entry or payment is created. Restrictive checks prevent linking
any different document/order and avoid counting the same physical goods twice.
"""
from decimal import Decimal
from django.db import migrations


def link_omega(apps, schema_editor):
    Order = apps.get_model("procurement", "PurchaseOrder")
    Goods = apps.get_model("inventory", "GoodsDocument")
    Stock = apps.get_model("inventory", "StockDocument")
    order = Order.objects.filter(pk=2, supplier__name__iexact="Omega").first()
    document = Goods.objects.filter(
        pk=1, kind="receipt", number="00ТК-029813",
        counterparty__iexact="Omega", warehouse="PP2",
    ).first()
    if not order or not document:
        return
    if document.purchase_order_id not in (None, order.pk):
        return
    if len(order.items) != 22:
        return
    if sum(int(line["quantity"]) for line in order.items) != 27:
        return
    expected_total = sum(
        Decimal(str(row["cost"])) * int(row["quantity"]) for row in order.items
    )
    if expected_total != Decimal("141546.00"):
        return
    docs = list(Stock.objects.filter(
        bundle_id=document.pk, kind="receipt", status="posted"
    ).select_related("item"))
    if len(docs) != 22 or sum(d.quantity for d in docs) != 27:
        return
    if any(d.purchase_order_id not in (None, order.pk) for d in docs):
        return
    actual_total = sum(
        (Decimal(d.unit_cost) * d.quantity for d in docs), Decimal("0")
    ) if all(d.unit_cost is not None for d in docs) else None
    if actual_total != Decimal("141546.00"):
        return
    unmatched = {(str(row["article"]), str(row["brand"])): idx
                 for idx, row in enumerate(order.items)}
    if len(unmatched) != 22:
        return
    links = []
    for doc in docs:
        key = (doc.item.article, doc.item.brand)
        if key not in unmatched:
            return
        idx = unmatched.pop(key)
        source = order.items[idx]
        if doc.quantity != int(source["quantity"]):
            return
        if Decimal(doc.unit_cost) != Decimal(str(source["cost"])):
            return
        links.append((doc.pk, idx))
    if unmatched:
        return
    for pk, idx in links:
        Stock.objects.filter(pk=pk).update(
            purchase_order_id=order.pk, purchase_line=idx
        )
    Goods.objects.filter(pk=document.pk).update(purchase_order_id=order.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0016_importpreview_previewfile"),
        ("procurement", "0117_import_first_china_purchase"),
    ]
    operations = [
        migrations.RunPython(link_omega, migrations.RunPython.noop)
    ]
