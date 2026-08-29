from django.db import migrations


AG_PARTS_SLUG = 'ag-parts'
NEW_DESCRIPTION = (
    'AG Parts — представитель группы китайских производителей автозапчастей. '
    'Постоянные оптовые поставки салонных и масляных фильтров и свечей зажигания. '
    'Прямые оптовые цены для магазинов, продавцов автозапчастей и СТО.'
)


def update_ag_parts_description(apps, schema_editor):
    SellerProfile = apps.get_model('catalog', 'SellerProfile')
    SellerProfile.objects.filter(slug=AG_PARTS_SLUG).update(
        description=NEW_DESCRIPTION,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0024_catalogimportbatch_wholesale_update_audit'),
    ]

    operations = [
        migrations.RunPython(
            update_ag_parts_description,
            migrations.RunPython.noop,
        ),
    ]
