from django.db import migrations


def stage(apps, schema_editor):
    from catalog.reviewed_weekend_20261114_f import stage_review

    stage_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("procurement", "0076_reviewed_weekend_20261114_e")]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
