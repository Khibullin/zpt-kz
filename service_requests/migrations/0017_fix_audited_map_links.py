"""Owner-approved repair of 41 stored 2GIS links, 25 October 2026.

Only strip the imported label when both ID and the audited old string match.
Concurrent/manual edits are preserved. No contacts, directions or dispatch changes.
"""
from django.db import migrations

CORRECTIONS = [(39, 'Жайлау https://2gis.kz/almaty/geo/70000001046606964', 'https://2gis.kz/almaty/geo/70000001046606964'),
 (44,
  'R I k https://2gis.kz/almaty/geo/70000001087895138/76.912767,43.404934',
  'https://2gis.kz/almaty/geo/70000001087895138/76.912767,43.404934'),
 (47,
  'Daewoo-Service https://2gis.kz/almaty/geo/70000001086716836',
  'https://2gis.kz/almaty/geo/70000001086716836'),
 (54,
  'Автомаляр https://2gis.kz/almaty/geo/70000001090514512',
  'https://2gis.kz/almaty/geo/70000001090514512'),
 (55,
  'У Сереги https://2gis.kz/almaty/geo/70000001100889221/76.927938,43.348334',
  'https://2gis.kz/almaty/geo/70000001100889221/76.927938,43.348334'),
 (72,
  'Vag zone service https://2gis.kz/almaty/geo/70000001027945492',
  'https://2gis.kz/almaty/geo/70000001027945492'),
 (74,
  'Vag zone service https://2gis.kz/almaty/geo/70000001027945492',
  'https://2gis.kz/almaty/geo/70000001027945492'),
 (77, '777 https://2gis.kz/almaty/geo/70000001076387621', 'https://2gis.kz/almaty/geo/70000001076387621'),
 (79, 'Еркетай https://2gis.kz/almaty/geo/70000001052100115', 'https://2gis.kz/almaty/geo/70000001052100115'),
 (86,
  'Fit service https://2gis.kz/almaty/geo/70000001064195260/76.843360,43.354218',
  'https://2gis.kz/almaty/geo/70000001064195260/76.843360,43.354218'),
 (94,
  'СТО https://2gis.kz/almaty/geo/70000001097243072/76.893752,43.334604',
  'https://2gis.kz/almaty/geo/70000001097243072/76.893752,43.334604'),
 (108,
  'Автоэлектрики https://2gis.kz/almaty/geo/70000001091612936',
  'https://2gis.kz/almaty/geo/70000001091612936'),
 (121,
  'Zac pro https://2gis.kz/almaty/geo/70000001091841321',
  'https://2gis.kz/almaty/geo/70000001091841321'),
 (124,
  'Grand Auto Motors https://2gis.kz/almaty/geo/70000001090282317',
  'https://2gis.kz/almaty/geo/70000001090282317'),
 (129,
  'Айдар https://2gis.kz/almaty/geo/70000001104633309/77.232415,43.367339',
  'https://2gis.kz/almaty/geo/70000001104633309/77.232415,43.367339'),
 (131, 'СТО https://2gis.kz/almaty/geo/70000001045732425', 'https://2gis.kz/almaty/geo/70000001045732425'),
 (133, 'СТО https://2gis.kz/almaty/geo/70000001069312899', 'https://2gis.kz/almaty/geo/70000001069312899'),
 (147,
  'Легковой автосервис https://2gis.kz/almaty/geo/70000001113864727',
  'https://2gis.kz/almaty/geo/70000001113864727'),
 (148,
  'AtaAuto https://2gis.kz/almaty/geo/70000001103160255',
  'https://2gis.kz/almaty/geo/70000001103160255'),
 (149,
  'Arsenal https://2gis.kz/almaty/geo/70000001038275382',
  'https://2gis.kz/almaty/geo/70000001038275382'),
 (150,
  'IVB Detail Studio https://2gis.kz/almaty/geo/70000001090382995',
  'https://2gis.kz/almaty/geo/70000001090382995'),
 (154,
  'Bay detailing studio https://2gis.kz/almaty/geo/70000001104415473/76.907346,43.176327',
  'https://2gis.kz/almaty/geo/70000001104415473/76.907346,43.176327'),
 (155,
  'Kais service https://2gis.kz/almaty/geo/70000001101733467/76.922711,43.188485',
  'https://2gis.kz/almaty/geo/70000001101733467/76.922711,43.188485'),
 (156,
  'Vernyi https://2gis.kz/almaty/geo/70000001043634693/76.921219,43.193845',
  'https://2gis.kz/almaty/geo/70000001043634693/76.921219,43.193845'),
 (165, 'Бекзат https://2gis.kz/almaty/geo/70000001081053264', 'https://2gis.kz/almaty/geo/70000001081053264'),
 (166,
  'Auto Spa https://2gis.kz/almaty/geo/70000001038031161',
  'https://2gis.kz/almaty/geo/70000001038031161'),
 (167,
  'Automen Detailing https://2gis.kz/almaty/geo/70000001104414684',
  'https://2gis.kz/almaty/geo/70000001104414684'),
 (170,
  'Fz Detailing https://2gis.kz/almaty/geo/70000001109862885',
  'https://2gis.kz/almaty/geo/70000001109862885'),
 (186,
  'Auto Space Detailing https://2gis.kz/almaty/geo/70000001099456028',
  'https://2gis.kz/almaty/geo/70000001099456028'),
 (187,
  '4You motors & detailing https://2gis.kz/almaty/geo/70000001105312306',
  'https://2gis.kz/almaty/geo/70000001105312306'),
 (190,
  'Tole bi detailing https://2gis.kz/almaty/geo/70000001083544628',
  'https://2gis.kz/almaty/geo/70000001083544628'),
 (192,
  'Tiffany Detailing https://2gis.kz/almaty/geo/70000001105225331',
  'https://2gis.kz/almaty/geo/70000001105225331'),
 (193,
  'Futurecar Detailing https://2gis.kz/almaty/geo/70000001110843077',
  'https://2gis.kz/almaty/geo/70000001110843077'),
 (194,
  'Detail masters https://2gis.kz/almaty/geo/70000001086834229',
  'https://2gis.kz/almaty/geo/70000001086834229'),
 (195,
  '727 Detailing Studio https://2gis.kz/almaty/geo/70000001103329233',
  'https://2gis.kz/almaty/geo/70000001103329233'),
 (196,
  'BR Service https://2gis.kz/almaty/geo/70000001109833118',
  'https://2gis.kz/almaty/geo/70000001109833118'),
 (197,
  'Uzotti detailing https://2gis.kz/almaty/geo/70000001100639731',
  'https://2gis.kz/almaty/geo/70000001100639731'),
 (199,
  'Detailing_centr1 https://2gis.kz/almaty/geo/70000001112444083',
  'https://2gis.kz/almaty/geo/70000001112444083'),
 (200,
  'Aka Detailing https://2gis.kz/almaty/geo/70000001112763232',
  'https://2gis.kz/almaty/geo/70000001112763232'),
 (201,
  'Ali_Pdr_service https://2gis.kz/almaty/geo/70000001096466053',
  'https://2gis.kz/almaty/geo/70000001096466053'),
 (202,
  'AUTO_SVET_MISHA https://2gis.kz/almaty/geo/70000001069112686',
  'https://2gis.kz/almaty/geo/70000001069112686')]


def apply_corrections(apps, schema_editor, reverse=False):
    Seller = apps.get_model('service_requests', 'ServiceSeller')
    alias = schema_editor.connection.alias
    changed = 0
    for pk, old, new in CORRECTIONS:
        before, after = (new, old) if reverse else (old, new)
        changed += Seller.objects.using(alias).filter(pk=pk, map_link=before).update(map_link=after)
    return changed


def forwards(apps, schema_editor):
    apply_corrections(apps, schema_editor)


def backwards(apps, schema_editor):
    apply_corrections(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [('service_requests', '0016_import_almaty_sto_20261020')]
    operations = [migrations.RunPython(forwards, backwards)]
