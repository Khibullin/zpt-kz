"""Set the approved retail price for one exact AG Parts article."""

from django.db import migrations


def set_price(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    qs = Product.objects.using(schema_editor.connection.alias).filter(
        id=2135,
        article='2032047000',
    )
    if not qs.exists():
        # Fresh installations have no catalog rows until the import runs.
        return
    if qs.count() != 1:
        raise RuntimeError('Duplicate Product 2135 / 2032047000')
    current = qs.values_list('price', flat=True).get()
    if current == 2770:
        return
    if current != 4190:
        raise RuntimeError(f'Unexpected price for 2032047000: {current}')
    qs.update(price=2770)


class Migration(migrations.Migration):
    dependencies = [('catalog', '0041_ag_parts_fitment_last_eight')]

    operations = [migrations.RunPython(set_price, migrations.RunPython.noop)]
