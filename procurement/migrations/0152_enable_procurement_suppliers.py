"""Restore supplier metadata only; preserve all existing rows and histories."""
from django.db import migrations


def add_suppliers(apps, schema_editor):
    Supplier = apps.get_model('procurement', 'Supplier')
    for canonical, aliases in (
        ('Phaeton DC', ('Phaeton DC', 'Phaeton', 'Фаетон', 'Фаэтон')),
        ('ZakazAuto', ('ZakazAuto', 'ЗаказАвто', 'ZakazAuto — Костанай')),
    ):
        if not Supplier.objects.using(schema_editor.connection.alias).filter(name__in=aliases).exists():
            Supplier.objects.using(schema_editor.connection.alias).create(name=canonical)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0151_supplier_comparison_price_lists')]
    operations = [migrations.RunPython(add_suppliers, migrations.RunPython.noop)]
