"""Keep only the latest completed Omega price upload; preserve actual offers.

Approved cleanup 2026-11-22. Do NOT delete or modify procurement offers,
published retail products, Kaspi review tasks or purchase orders.
"""
from django.db import migrations


LATEST_FILENAME = "Прайс лист Solid Prof Group от 20.11.2026г.xlsx"
LATEST_DATE = "2026-11-20"


def keep_latest_omega_update(apps, schema_editor):
    Supplier = apps.get_model("procurement", "Supplier")
    PriceUpdate = apps.get_model("procurement", "PriceUpdate")
    KaspiReviewTask = apps.get_model("procurement", "KaspiReviewTask")
    ProfitableOfferRecord = apps.get_model("procurement", "ProfitableOfferRecord")
    alias = schema_editor.connection.alias

    omega = Supplier.objects.using(alias).get(name="Omega")
    newest = PriceUpdate.objects.using(alias).get(
        supplier_id=omega.pk, filename=LATEST_FILENAME,
        status="done",
    )
    if str(newest.checked_on) != LATEST_DATE:
        raise RuntimeError("Omega latest price date differs; cleanup aborted")
    if newest.row_count != 19855:
        raise RuntimeError("Omega latest price row count differs; cleanup aborted")

    obsolete = PriceUpdate.objects.using(alias).filter(
        supplier_id=omega.pk,
    ).exclude(pk=newest.pk)
    old_ids = list(obsolete.values_list("pk", flat=True))
    if not old_ids:
        return
    # Keep sourcing/review work but remove references to obsolete upload history.
    KaspiReviewTask.objects.using(alias).filter(batch_id__in=old_ids).update(batch_id=None)
    ProfitableOfferRecord.objects.using(alias).filter(
        first_batch_id__in=old_ids
    ).update(first_batch_id=None)
    obsolete.delete()


class Migration(migrations.Migration):
    atomic = True
    dependencies = [
        ("procurement", "0149_enrich_118_omega_kaspi_evidence"),
    ]
    operations = [
        migrations.RunPython(keep_latest_omega_update, migrations.RunPython.noop),
    ]
