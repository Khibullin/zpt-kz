from django.db import migrations


def stage(apps, schema_editor):
    from catalog.reviewed_weekend_20261114_l import stage_review

    stage_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("procurement", "0083_reviewed_weekend_20261114_k")]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
