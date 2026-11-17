"""Load the 17 November 2026 authorized Firsthand sample into existing Omega offers.

Only 41 existing offers are touched; no new product records are created.
Nothing in the migration authorizes purchase or publishes a Kaspi listing.
"""
from django.db import migrations


def forward(apps, schema_editor):
    from procurement.firsthand_omega_import import import_omega_firsthand
    Offer = apps.get_model("procurement", "Offer")
    Snapshot = apps.get_model("procurement", "KaspiSellerSnapshot")
    import_omega_firsthand(Offer, Snapshot)


class Migration(migrations.Migration):
    dependencies = [("procurement", "0119_queue_next_omega_fifty")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
