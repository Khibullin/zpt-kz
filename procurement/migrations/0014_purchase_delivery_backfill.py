from datetime import date
from decimal import Decimal
from django.db import migrations


def forward(apps,schema_editor):
    Order=apps.get_model('procurement','PurchaseOrder')
    for old,new in [('sent','sent'),('confirmed','waiting'),('paid','waiting'),('received','waiting'),('cancelled','cancelled')]:
        Order.objects.filter(status=old,delivery_stage='draft').update(delivery_stage=new)
    order=Order.objects.filter(pk=2,supplier__name__iexact='Omega',archived=False).first()
    # Apply the owner's concrete plan only to the exact uploaded order.
    if order and len(order.items)==22 and sum(i['quantity'] for i in order.items)==27 and sum((Decimal(str(i['cost']))*i['quantity'] for i in order.items),Decimal(0))==Decimal('141546'):
        order.destination='PP2';order.expected_date=date(2026,11,5);order.delivery_stage='waiting'
        order.title='Омега · ЗК-000002 · поставка 05.11.2026'
        order.save(update_fields=['destination','expected_date','delivery_stage','title'])


class Migration(migrations.Migration):
    dependencies=[('procurement','0013_purchaseorder_archived_purchaseorder_delivery_stage_and_more')]
    operations=[migrations.RunPython(forward,migrations.RunPython.noop)]
