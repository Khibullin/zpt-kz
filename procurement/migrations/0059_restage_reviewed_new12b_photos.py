from django.db import migrations

def stage(apps, schema_editor):
    from catalog.reviewed_new12a import stage_review
    stage_review(apps, schema_editor)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0058_apply_new12a_supplier_identity')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
