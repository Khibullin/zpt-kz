from django.db import migrations

def stage(apps,schema_editor):
    from catalog.reviewed_phaeton_resume9 import stage_review
    stage_review(apps,schema_editor)

class Migration(migrations.Migration):
    dependencies=[('procurement','0055_reviewed_phaeton_resume8')]
    operations=[migrations.RunPython(stage,migrations.RunPython.noop)]
