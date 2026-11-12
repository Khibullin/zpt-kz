from django.db import migrations

def stage(apps, schema_editor):
    from catalog.reviewed_new12d import stage_review
    stage_review(apps, schema_editor)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0060_reviewed_new12c_fitment')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
