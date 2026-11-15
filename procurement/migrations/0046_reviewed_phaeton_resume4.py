from django.db import migrations

def stage(apps,schema_editor):
    from catalog.reviewed_phaeton_resume4 import stage_review
    stage_review(apps,schema_editor)

class Migration(migrations.Migration):
    dependencies=[('procurement','0045_reviewed_phaeton_resume3')]
    operations=[migrations.RunPython(stage,migrations.RunPython.noop)]
