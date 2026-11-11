from django.db import migrations

def stage(apps,schema_editor):
    from catalog.reviewed_phaeton_resume2 import stage_review
    stage_review(apps,schema_editor)

class Migration(migrations.Migration):
    dependencies=[('procurement','0043_reviewed_phaeton_resume1')]
    operations=[migrations.RunPython(stage,migrations.RunPython.noop)]
