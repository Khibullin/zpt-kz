from django.db import migrations

def stage(apps, schema_editor):
    from catalog.reviewed_new12a import stage_review
    stage_review(apps, schema_editor)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0056_reviewed_phaeton_resume9')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
