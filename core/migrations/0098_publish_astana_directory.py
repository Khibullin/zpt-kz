"""Publish reviewed existing Astana sellers without changing request eligibility."""
from django.db import migrations

# Existing seller records reviewed on 18 October; distinct saved contacts.
# Repeated names without addresses, service-only candidates and city conflicts held.
SELLERS = [
  [
    309,
    "АвтоАлем",
    "77475880238"
  ],
  [
    318,
    "V-Parts",
    "77022518808"
  ],
  [
    324,
    "Auto Life",
    "77017945601"
  ],
  [
    326,
    "BV PARTSCOM",
    "77023211800"
  ],
  [
    327,
    "Автозапчасти — Ходовая, двигатель",
    "77017547169"
  ],
  [
    328,
    "Автозапчасти в наличии и на заказ",
    "77785047874"
  ],
  [
    329,
    "Автозапчасти на китайские авто",
    "77770382684"
  ],
  [
    340,
    "QAZYNA MOTORS",
    "77479220626"
  ],
  [
    346,
    "Автозапчасти на японские авто",
    "77015138368"
  ],
  [
    375,
    "Жанабергенов Мирас / Berlin auto",
    "77083850301"
  ],
  [
    378,
    "Магазин 208 бутик",
    "77015266502"
  ],
  [
    438,
    "Kaizen Auto Astana",
    "77475539395"
  ],
  [
    439,
    "Новые Двигатели",
    "77074757798"
  ],
  [
    440,
    "NDParts",
    "77086644894"
  ],
  [
    441,
    "Вся Корея",
    "77711702262"
  ],
  [
    442,
    "Pitstop",
    "77019606151"
  ],
  [
    444,
    "Корея Моторс",
    "77021347137"
  ],
  [
    457,
    "ИНТЕРКОМ",
    "77020757275"
  ],
  [
    578,
    "Сервис запчасти диагностика и ремонт Киа Астана repair-auto.kz",
    "77071075738"
  ],
  [
    594,
    "Запчасти в Кокшетау",
    "77786857788"
  ],
  [
    595,
    "Hyundai/Kia",
    "77087172549"
  ],
  [
    598,
    "ASTANA-PARTS.KZ",
    "77072466068"
  ],
  [
    673,
    "Korean Autoparts — Астана",
    "77015324486"
  ],
  [
    694,
    "TIREK PARTS",
    "77476040989"
  ]
]

def publish(apps):
    Seller = apps.get_model('core', 'Seller')
    changed = 0
    for pk, expected_name, phone in SELLERS:
        seller = Seller.objects.select_for_update().get(pk=pk)
        name = 'Avenue Auto' if pk == 594 else expected_name
        if (seller.name not in (expected_name, name) or seller.whatsapp != phone
                or seller.city != 'Астана' or seller.is_dismantler
                or not seller.is_active or seller.is_paused or seller.is_test_seller):
            raise RuntimeError('Astana directory identity/status changed: %s' % pk)
        updates = {}
        if not seller.directory_parts:
            updates['directory_parts'] = True
        if seller.name != name:
            updates['name'] = name
        if updates:
            Seller.objects.filter(pk=pk).update(**updates)
            changed += 1
    return changed

def load(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql' or connection.settings_dict.get('NAME') != 'zpt_kz':
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [202610180098])
    print('ASTANA_DIRECTORY published:', publish(apps))

class Migration(migrations.Migration):
    dependencies = [('core', '0097_public_directory_details')]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]

