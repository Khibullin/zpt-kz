from django.db import migrations


def cleanup_unsafe_oneoff_activations(apps, schema_editor):
    Seller = apps.get_model('core', 'Seller')
    SellerLead = apps.get_model('core', 'SellerLead')

    # Epart.kz was found while searching Konaev, but the business title itself
    # explicitly identifies Pavlodar. The seller is valid for Kazakhstan, so
    # keep the activation and correct both linked records to Pavlodar.
    lead = SellerLead.objects.filter(
        pk=183,
        request_seller_id=564,
        whatsapp='77754343424',
    ).first()
    seller = Seller.objects.filter(
        pk=564,
        user_id__isnull=True,
        whatsapp='77754343424',
        notes__contains='SellerLead #183',
    ).first()
    if lead is not None and seller is not None:
        seller.city = 'Павлодар'
        seller.save(update_fields=['city'])

        lead.city = 'Павлодар'
        lead.lifecycle_status = 'active'
        lead.review_status = 'converted_requests'
        lead.status = 'needs_review'
        lead.market_scope = 'kz'
        lead.market_scope_evidence = 'explicit business title: г. Павлодар'
        lead.save(update_fields=[
            'city',
            'lifecycle_status',
            'review_status',
            'status',
            'market_scope',
            'market_scope_evidence',
            'updated_at',
        ])

    # This result is a Russian seller (.рф, +7 951, "отправим по РФ") that was
    # incorrectly assigned the search city Uralsk. It must never receive KZ
    # buyer requests.
    lead = SellerLead.objects.filter(
        pk=201,
        request_seller_id=565,
        whatsapp='79512325963',
    ).first()
    if lead is not None:
        lead.request_seller_id = None
        lead.request_seller_transport_type = ''
        lead.city = ''
        lead.lifecycle_status = 'classified'
        lead.review_status = 'needs_review'
        lead.status = 'needs_review'
        lead.market_scope = 'foreign'
        lead.market_scope_evidence = 'identity=.рф,+7-9xx,РФ'
        lead.reviewed_at = None
        lead.save(update_fields=[
            'request_seller',
            'request_seller_transport_type',
            'city',
            'lifecycle_status',
            'review_status',
            'status',
            'market_scope',
            'market_scope_evidence',
            'reviewed_at',
            'updated_at',
        ])

    Seller.objects.filter(
        pk=565,
        user_id__isnull=True,
        whatsapp='79512325963',
        notes__contains='SellerLead #201',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0070_request_routing_strategy'),
    ]

    operations = [
        migrations.RunPython(
            cleanup_unsafe_oneoff_activations,
            migrations.RunPython.noop,
        ),
    ]
