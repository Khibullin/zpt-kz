from django.db import migrations

def stage(apps, schema_editor):
    from catalog.reviewed_remaining2 import stage_review
    stage_review(apps, schema_editor)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0031_restore_two_reviewed_photos')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
