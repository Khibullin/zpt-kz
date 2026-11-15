from django.db import migrations


def stage(apps, schema_editor):
    from catalog.reviewed_weekend_20261114_j import stage_review

    stage_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("procurement", "0081_reviewed_weekend_20261114_i")]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
