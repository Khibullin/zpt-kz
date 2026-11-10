from django.db import migrations


def stage(apps,schema_editor):
    from catalog.photo_queue import stage_all_photos
    stage_all_photos(apps)


class Migration(migrations.Migration):
    dependencies=[('catalog','0085_stage_all_supplier_photos')]
    operations=[migrations.RunPython(stage,migrations.RunPython.noop)]
