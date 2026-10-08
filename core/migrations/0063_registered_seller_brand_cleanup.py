from django.db import migrations


def preserve_existing_specialization(apps, schema_editor):
    """Keep registered sellers' existing core.Brand selections unchanged.

    The directory audit initially compared these relations against catalog.Brand,
    which is a separate product-catalog taxonomy. Production verification showed
    that Seller.selected_brands already points to core.Brand and the specialist
    mappings are coherent, so this migration deliberately makes no data change.
    """
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0062_registered_seller_directory_cleanup'),
    ]

    operations = [
        migrations.RunPython(
            preserve_existing_specialization,
            migrations.RunPython.noop,
        ),
    ]
