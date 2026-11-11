from django.db import migrations

def stage(apps, schema_editor):
    from catalog.reviewed_zakaz_bumpers4 import stage_review
    stage_review(apps, schema_editor)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0035_reviewed_zakaz_cvvt')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
