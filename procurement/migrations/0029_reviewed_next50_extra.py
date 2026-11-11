from django.db import migrations


def stage(apps, schema_editor):
    from catalog.reviewed_next50_extra import stage_review
    stage_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0028_reviewed_next50_photos_and_fitments')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
