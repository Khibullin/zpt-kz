from django.db import migrations


def configure_ag_parts_wholesale_terms(apps, schema_editor):
    SellerProfile = apps.get_model('catalog', 'SellerProfile')
    SellerWholesaleTerms = apps.get_model('catalog', 'SellerWholesaleTerms')

    try:
        seller = SellerProfile.objects.get(slug='ag-parts')
    except SellerProfile.DoesNotExist:
        raise RuntimeError("SellerProfile with slug='ag-parts' was not found.")

    if seller.wholesale_enabled != True:
        raise RuntimeError(
            'AG Parts wholesale_enabled is not True. Operation aborted.'
        )

    SellerWholesaleTerms.objects.update_or_create(
        seller_id=seller.pk,
        defaults={
            'vat_mode': 'included',
            'prepayment_percent': 100,
            'confirm_stock_before_payment': True,
            'provides_invoice': True,
            'provides_waybill': True,
            'provides_esf': True,
            'pickup_enabled': True,
            'pickup_city': 'Алматы',
            'delivery_kz_enabled': True,
            'delivery_payer': 'buyer',
            'primary_carrier': 'DPD Kazakhstan',
            'primary_carrier_service': 'DPD OPTIMUM',
            'primary_carrier_url': 'https://dpd.kz/',
            'other_carrier_allowed': True,
            'stock_note': 'Наличие подтверждается перед оплатой.',
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0022_sellerwholesaleterms'),
    ]

    operations = [
        migrations.RunPython(
            configure_ag_parts_wholesale_terms,
            migrations.RunPython.noop,
        ),
    ]
