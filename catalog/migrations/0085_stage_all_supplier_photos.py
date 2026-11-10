from django.db import migrations


def stage(apps,schema_editor):
    from catalog.photo_queue import stage_all_photos
    stage_all_photos(apps)


class Migration(migrations.Migration):
    dependencies=[('catalog','0084_recheck_supplier_article_formatting')]
    operations=[migrations.RunPython(stage,migrations.RunPython.noop)]
