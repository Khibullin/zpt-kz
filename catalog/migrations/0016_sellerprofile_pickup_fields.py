from django.db import migrations, models


def forwards_populate_pickup_fields(apps, schema_editor):
    SellerProfile = apps.get_model('catalog', 'SellerProfile')
    for seller in SellerProfile.objects.all().iterator():
        address = (seller.address or '').strip()
        if address:
            seller.pickup_address = address
            seller.pickup_same_as_store = True
            seller.pickup_available = True
        else:
            seller.pickup_address = ''
            seller.pickup_same_as_store = True
            seller.pickup_available = False
        seller.save(update_fields=[
            'pickup_address',
            'pickup_same_as_store',
            'pickup_available',
        ])


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0015_sellerprofile_default_work_hours_delivery'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sellerprofile',
            name='address',
            field=models.CharField(
                blank=True,
                default='',
                max_length=500,
                verbose_name='Адрес магазина / офиса',
            ),
        ),
        migrations.AddField(
            model_name='sellerprofile',
            name='pickup_address',
            field=models.CharField(
                blank=True,
                default='',
                max_length=500,
                verbose_name='Адрес самовывоза',
            ),
        ),
        migrations.AddField(
            model_name='sellerprofile',
            name='pickup_available',
            field=models.BooleanField(
                default=True,
                verbose_name='Самовывоз доступен',
            ),
        ),
        migrations.AddField(
            model_name='sellerprofile',
            name='pickup_same_as_store',
            field=models.BooleanField(
                default=True,
                verbose_name='Адрес самовывоза совпадает с адресом магазина',
            ),
        ),
        migrations.RunPython(forwards_populate_pickup_fields, backwards_noop),
    ]
