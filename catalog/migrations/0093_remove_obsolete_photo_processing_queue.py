"""Remove obsolete photo-processing queue, not the product photos themselves.

Approved ZPT.KZ cleanup, 2026-11-22. The public catalogue, supplier offers,
published retail listings and all image/fitment fields remain untouched.
"""
from django.db import migrations


def clear_photo_queue(apps, schema_editor):
    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Task.objects.using(schema_editor.connection.alias).all().delete()


class Migration(migrations.Migration):
    atomic = True
    dependencies = [
        ("catalog", "0092_supplier_photo_task_lookup_indexes"),
    ]
    operations = [
        migrations.RunPython(clear_photo_queue, migrations.RunPython.noop),
    ]
