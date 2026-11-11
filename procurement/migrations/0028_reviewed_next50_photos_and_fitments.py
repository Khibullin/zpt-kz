"""Fill exact, reviewed photographs and fitments in the owner-selected next 50."""
from django.db import migrations


def stage(apps, schema_editor):
    from catalog.reviewed_next50 import stage_review
    stage_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0027_reviewed_next50_bilstein_photos')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
