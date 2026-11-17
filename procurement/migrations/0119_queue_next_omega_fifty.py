"""Queue the next 50 in-stock Omega products above 2000 KZT for Kaspi monitoring.

Only queue items; do not mark unverified Kaspi listings or historical prices
as confirmed, profitable, or freshly checked. All existing UI/calculation rules
remain untouched.
"""
from django.db import migrations

OMEGA_OFFER_IDS = (22, 26, 27, 28, 30, 31, 34, 35, 36, 38, 39, 41, 43, 44, 45, 46, 48, 49, 51, 52, 53, 56, 62, 63, 64, 65, 66, 67, 69, 70, 72, 74, 76, 77, 79, 80, 81, 82, 83, 84, 86, 87, 88, 90, 93, 94, 95, 98, 107, 108)


def enqueue_next_omega_fifty(apps, schema_editor):
    Offer = apps.get_model("procurement", "Offer")
    ReviewTask = apps.get_model("procurement", "KaspiReviewTask")
    eligible = Offer.objects.filter(
        pk__in=OMEGA_OFFER_IDS, supplier__name="Omega", cost__gt=2000,
    ).exclude(stock="0").exclude(stock="")
    if eligible.count() != len(OMEGA_OFFER_IDS):
        raise ValueError("Omega 50 batch changed: check stock and supplier before queueing")
    for offer in eligible:
        ReviewTask.objects.get_or_create(
            offer_id=offer.pk,
            defaults={
                "status": "pending",
                "priority": 1,
                "reason": "Omega: следующая партия 50; требуется подтверждение карточки и цен Kaspi по городам Казахстана",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0118_queue_omega_ten_kaspi_review")]
    operations = [migrations.RunPython(enqueue_next_omega_fifty, migrations.RunPython.noop)]
