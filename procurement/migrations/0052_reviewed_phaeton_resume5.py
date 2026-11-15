from django.db import migrations

def stage(apps,schema_editor):
    from catalog.reviewed_phaeton_resume5 import stage_review
    stage_review(apps,schema_editor)

class Migration(migrations.Migration):
    dependencies=[('procurement','0051_reviewed_zakaz_resume26')]
    operations=[migrations.RunPython(stage,migrations.RunPython.noop)]
