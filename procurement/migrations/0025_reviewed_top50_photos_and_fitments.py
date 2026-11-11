"""Fill only the owner-selected, independently reviewed Phaeton priorities."""
from django.db import migrations


def stage(apps, schema_editor):
    from catalog.reviewed_top50 import stage_review
    stage_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0024_reviewed_top50_bilstein')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
