from django.db import migrations


def stage(apps, schema_editor):
    from catalog.reviewed_weekend_20261114_n import stage_review

    stage_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("procurement", "0086_reviewed_weekend_20261114_m_years")]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
