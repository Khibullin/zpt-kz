from django.db import migrations


def stage(apps, schema_editor):
    from catalog.reviewed_nightly_20261114 import stage_review

    stage_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("procurement", "0062_filter_dictionary_indexes")]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
