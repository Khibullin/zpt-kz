from django.db import migrations


def backfill(apps, schema_editor):
    StockDocument=apps.get_model('inventory','StockDocument')
    GoodsDocument=apps.get_model('inventory','GoodsDocument')
    StockEntry=apps.get_model('inventory','StockEntry')
    for doc in StockDocument.objects.filter(bundle__isnull=True).iterator():
        defaults=dict(kind=doc.kind,number=doc.external_number,date=doc.date,accepted_on=doc.date,warehouse=doc.warehouse,destination=doc.destination,counterparty=doc.counterparty,purchase_order_id=doc.purchase_order_id,created_by_id=doc.created_by_id)
        if doc.receipt_key:
            bundle,_=GoodsDocument.objects.get_or_create(request_key=doc.receipt_key,defaults=defaults)
        else:
            bundle=GoodsDocument.objects.create(**defaults)
        StockDocument.objects.filter(pk=doc.pk).update(bundle_id=bundle.pk)
    from django.db.models import OuterRef, Subquery
    StockEntry.objects.filter(effective_on__isnull=True).update(effective_on=Subquery(StockDocument.objects.filter(pk=OuterRef('document_id')).values('date')[:1]))


class Migration(migrations.Migration):
    dependencies=[('inventory','0011_importbatch_original_data_stockentry_effective_on_and_more')]
    operations=[migrations.RunPython(backfill,migrations.RunPython.noop)]
