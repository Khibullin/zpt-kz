"""Restore five source-verified addresses without changing routing geography.

2GIS company IDs were checked on 25 October 2026. Preserve current edits,
city fields, account identity, notification preferences and dispatch rules.
"""
from django.db import migrations

# ID, expected city, expected saved 2GIS URL, old address, verified address.
CORRECTIONS = [
    (204, 'Талдыкорган', 'https://2gis.kz/almaty/firm/70000001116295102', '',
     'улица Желтоксан, 224'),
    (347, '', 'https://2gis.ru/firm/70000001111385634', 'Самал-2 микрорайон, 266',
     'с. Туздыбастау, Самал-2 микрорайон, 266'),
    (382, '', 'https://2gis.ru/firm/70000001086838893', 'улица Майкы Би, 2Б',
     'с. Бесагаш, улица Майкы Би, 2Б'),
    (524, '', 'https://2gis.ru/firm/70000001116122201', 'улица Гаухартас, 4',
     'с. Жанатурмыс, улица Гаухартас, 4'),
    (843, '', 'https://2gis.ru/firm/70000001104636255', '',
     'с. Жамбыл, Карасайский район, Алматинская область'),
]


def apply_corrections(apps, schema_editor, reverse=False):
    Seller = apps.get_model('service_requests', 'ServiceSeller')
    changed = 0
    for pk, city, url, old, new in CORRECTIONS:
        before, after = (new, old) if reverse else (old, new)
        changed += Seller.objects.using(schema_editor.connection.alias).filter(
            pk=pk, city=city, map_link=url, address=before).update(address=after)
    return changed


def forwards(apps, schema_editor):
    apply_corrections(apps, schema_editor)


def backwards(apps, schema_editor):
    apply_corrections(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [('service_requests', '0017_fix_audited_map_links')]
    operations = [migrations.RunPython(forwards, backwards)]
