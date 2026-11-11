from django.db import migrations

def stage(apps,schema_editor):
    from catalog.reviewed_phaeton_resume1 import stage_review
    stage_review(apps,schema_editor)

class Migration(migrations.Migration):
    dependencies=[('procurement','0042_reviewed_zakaz_resume21')]
    operations=[migrations.RunPython(stage,migrations.RunPython.noop)]
