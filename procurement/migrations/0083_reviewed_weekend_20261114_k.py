from django.db import migrations

def stage(apps,schema_editor):
    from catalog.reviewed_weekend_20261114_k import stage_review
    stage_review(apps,schema_editor)

class Migration(migrations.Migration):
    dependencies=[("procurement","0082_reviewed_weekend_20261114_j")]
    operations=[migrations.RunPython(stage,migrations.RunPython.noop)]
