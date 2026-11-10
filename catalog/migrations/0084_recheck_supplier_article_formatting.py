from django.db import migrations


def recheck(apps, schema_editor):
    Task=apps.get_model('catalog','SupplierPhotoTask')
    Task.objects.filter(status='skipped',
        note='Точное совпадение или реальная фотография не найдены.'
    ).update(status='pending',note='Повторная сверка оформления артикула по названию того же товара.')


class Migration(migrations.Migration):
    dependencies=[('catalog','0083_economical_supplier_photos')]
    operations=[migrations.RunPython(recheck,migrations.RunPython.noop)]
