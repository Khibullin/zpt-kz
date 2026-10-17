"""Conservative corrections from existing ZPT.KZ seller records (17 Oct 2026).

Never infer 'new parts' simply from absence of the dismantler flag, and
never mistake vehicle dealers for confirmed spare-parts retailers.
This migration only touches specific existing seller records by ID and
unchanged name; it does not create sellers, change their contacts, edit
imported inventory or alter WhatsApp dispatch settings.
"""
from django.db import migrations


EXPLICIT_DISMANTLERS = (
    (133, "Razbor777.kz"),
    (173, "Subaru разбор"),
    (180, "Subaru разбор"),
    (364, "Центр авторазбора"),
    (380, "Центр авторазбора из США и Японии"),
    (382, "Авторазбор на Уштобе"),
    (383, "Авторазбор на Уштобе"),
    (384, "Авторазбор09"),
    (407, "Центр авторазбора"),
    (408, "Центр авторазбора"),
    (411, "Центр авторазбора"),
    (427, "Rx Razbor"),
    (428, "Центр авторазбора"),
    (430, "Центр авторазбора для корейских автомобилей"),
    (434, "Japan Autorazbor"),
    (435, "Центр авторазбора"),
    (436, "Razbor.mb"),
)

NEW_PARTS_REQUIRE_REVIEW = (
    (446, "BMW Алматы (@bavaria_almaty) • Instagram photos and videos"),
    (544, "Mercedes-Benz Almaty (@mercedesbenz.almaty) • Instagram photos and videos"),
    (547, "Blue Star of Kazakhstan (@mercedes_benz_bsk) • Instagram photos and videos"),
    (550, "Audi Centre Almaty (@audi_centre_almaty) • Instagram photos and videos"),
    (551, "Официальный дилер Hyundai | Алматы (@hyundai_kuldzhinka) • Instagram photos and videos"),
    (617, "Тойота Центр Жетысу | Официальный дилер (@toyota7su_official) • Instagram photos and videos"),
    (622, "Toyota City Almaty - самые выгодные предложения (@toyota_city_almaty) • Instagram photos and videos"),
    (641, "Тойота Алматы (@toyota.almaty) • Instagram photos and videos"),
    (628, "Kparts"),
    (648, "MB Parts KZ"),
    (670, "Авторазбор BUU — Toyota Camry"),
    (672, "Japan Parts — авторазбор Toyota и Lexus"),
    (677, "DAT Motors Almaty"),
)


def correct_existing_seller_specializations(apps, schema_editor):
    Seller = apps.get_model("core", "Seller")

    # Their own recorded business names explicitly identify dismantling.
    for pk, expected_name in EXPLICIT_DISMANTLERS:
        Seller.objects.filter(
            pk=pk,
            name=expected_name,
            is_dismantler=False,
        ).update(is_dismantler=True)

    # 'Dealer' describes vehicle sales, not necessarily spare-parts sales.
    # Five existing dismantlers also inherited the 'new_parts' label solely
    # from generic text about parts. Preserve their dismantler status.
    for pk, expected_name in NEW_PARTS_REQUIRE_REVIEW:
        Seller.objects.filter(
            pk=pk,
            name=expected_name,
            sells_new_parts=True,
        ).update(sells_new_parts=None)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0088_confirm_new_parts_from_catalog"),
    ]

    operations = [
        migrations.RunPython(
            correct_existing_seller_specializations,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
