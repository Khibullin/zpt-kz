from django.db import migrations, models


def stage(apps, schema_editor):
    Listing=apps.get_model('procurement','RetailListing')
    Task=apps.get_model('catalog','SupplierPhotoTask')
    candidates=Listing.objects.filter(
        enabled=True,offer__supplier__name='Omega',
        offer__supplier_url__startswith='https://omega-auto-parts.kz/',
        product__status='active',product__stock_qty__gt=0,
    ).filter(models.Q(product__main_image='')|models.Q(product__main_image__isnull=True)
    ).exclude(product_id__in=Task.objects.values('product_id')).annotate(
        priority=models.Case(models.When(offer__name__icontains='фильтр',then=models.Value(0)),
        default=models.Value(1),output_field=models.IntegerField())
    ).select_related('offer').order_by('priority','pk')[:1000]
    Task.objects.bulk_create([Task(product_id=row.product_id,offer_id=row.offer_id,
        source_url=row.offer.supplier_url) for row in candidates],ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies=[('catalog','0082_stage_supplier_photo_pilot')]
    operations=[
        migrations.AddField(model_name='product',name='supplier_thumbnail',
            field=models.ImageField(upload_to='products/',blank=True,default='')),
        migrations.RunPython(stage,migrations.RunPython.noop),
    ]
