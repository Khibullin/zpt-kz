from django.db import migrations


def seed(apps, schema_editor):
    from procurement.filter_snapshots import refresh_filter_snapshots
    refresh_filter_snapshots(apps=apps, force=True)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('procurement','0063_kaspi_prepared_cards'),('catalog','0091_stage_twenty_nine_reviewed_aywiparts_sources')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
