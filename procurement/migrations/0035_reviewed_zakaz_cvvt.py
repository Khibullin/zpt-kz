from django.db import migrations

def stage(apps, schema_editor):
    from catalog.reviewed_zakaz_cvvt import stage_review
    stage_review(apps, schema_editor)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0034_reviewed_zakaz_continuation2')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
