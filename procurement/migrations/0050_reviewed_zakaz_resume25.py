from django.db import migrations

def stage(apps,schema_editor):
    from catalog.reviewed_zakaz_resume25 import stage_review
    stage_review(apps,schema_editor)

class Migration(migrations.Migration):
    dependencies=[('procurement','0049_reviewed_zakaz_resume24')]
    operations=[migrations.RunPython(stage,migrations.RunPython.noop)]
