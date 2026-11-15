from django.db import migrations


def stage(apps, schema_editor):
    from catalog.reviewed_weekend_20261114_g import stage_review

    stage_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("procurement", "0077_reviewed_weekend_20261114_f")]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
