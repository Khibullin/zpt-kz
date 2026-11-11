from django.db import migrations

def stage(apps, schema_editor):
    from catalog.reviewed_zakaz100 import stage_review
    stage_review(apps, schema_editor)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0029_reviewed_next50_extra')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
