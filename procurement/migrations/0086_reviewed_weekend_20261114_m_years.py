from django.db import migrations


def repair(apps, schema_editor):
    from catalog.reviewed_weekend_20261114_m import repair_year_labels

    repair_year_labels(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("procurement", "0085_reviewed_weekend_20261114_m")]
    operations = [migrations.RunPython(repair, migrations.RunPython.noop)]
