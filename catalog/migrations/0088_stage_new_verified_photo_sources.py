"""Stage newly verified manufacturer sources through the existing photo queue."""
from django.db import migrations


def stage(apps, schema_editor):
    from catalog.photo_queue import stage_all_photos
    stage_all_photos(apps)


class Migration(migrations.Migration):
    dependencies = [('catalog', '0087_stage_public_web_photos')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
