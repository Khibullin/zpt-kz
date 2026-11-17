"""Register the ten Omega items selected for Kaspi review on 2026-11-17.

No inferred seller prices, no eligibility changes, no overwrite of existing
reviews. This is a resumable queue, not a declaration of profitability.
"""
from django.db import migrations

OMEGA_OFFER_IDS = (3, 6, 10, 13, 15, 16, 18, 20, 21, 13411)


def enqueue_omega_ten(apps, schema_editor):
    Offer = apps.get_model("procurement", "Offer")
    ReviewTask = apps.get_model("procurement", "KaspiReviewTask")
    for offer in Offer.objects.filter(pk__in=OMEGA_OFFER_IDS, supplier__name="Omega", cost__gt=2000).exclude(stock="0").exclude(stock=""):
        ReviewTask.objects.get_or_create(
            offer_id=offer.pk,
            defaults={
                "status": "pending",
                "priority": 1,
                "reason": "Omega: партия 10 от 17.11.2026; проверка карточки и актуальных цен Kaspi по Казахстану",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0117_import_first_china_purchase")]
    operations = [migrations.RunPython(enqueue_omega_ten, migrations.RunPython.noop)]
