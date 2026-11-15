from django.db import migrations

def stage(apps, schema_editor):
    from catalog.reviewed_zakaz_resume8 import stage_review
    stage_review(apps, schema_editor)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0039_reviewed_zakaz_resume4')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
