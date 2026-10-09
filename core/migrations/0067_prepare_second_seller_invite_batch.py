from django.db import migrations
from django.utils import timezone


SECOND_BATCH = {
    4: ('77002510460', 'new_parts'),
    37: ('77752011121', 'new_parts'),
    44: ('77072900900', 'service_parts'),
    47: ('77479262605', 'new_parts'),
    57: ('77474175577', 'new_parts'),
    60: ('77773272874', 'dismantler'),
    113: ('77064305115', 'new_parts'),
    114: ('77772053552', 'new_parts'),
    116: ('77071212323', 'new_parts'),
    118: ('77477788886', 'new_parts'),
    125: ('77074556350', 'new_parts'),
}

PREPARABLE_LIFECYCLES = (
    'found',
    'enriched',
    'classified',
    'ready_to_invite',
)


def prepare_second_invite_batch(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    Seller = apps.get_model('core', 'Seller')
    now = timezone.now()

    for lead_id, (whatsapp, business_type) in SECOND_BATCH.items():
        qs = SellerLead.objects.filter(
            pk=lead_id,
            whatsapp=whatsapp,
            whatsapp_confidence='high',
            market_scope='kz',
            duplicate_of__isnull=True,
        ).exclude(
            lifecycle_status__in=('invited', 'registered', 'rejected', 'duplicate', 'closed', 'unreachable'),
        )
        qs.update(
            business_type=business_type,
            business_type_confidence=100,
            business_type_evidence='Ручная проверка: вторая WhatsApp-очередь приглашений 09.10.2026',
            checked_at=now,
            reviewed_at=now,
            updated_at=now,
        )
        qs.filter(lifecycle_status__in=PREPARABLE_LIFECYCLES).update(
            lifecycle_status='ready_to_invite',
            updated_at=now,
        )

    # AUTOLEADER is already a registered seller under the same confirmed
    # WhatsApp number. Link the discovery card to that seller and keep it out
    # of future invitation batches.
    seller = Seller.objects.filter(
        pk=415,
        whatsapp='77759772338',
        user_id__isnull=False,
    ).first()
    if seller is not None:
        SellerLead.objects.filter(
            pk=126,
            whatsapp='77759772338',
            duplicate_of__isnull=True,
        ).update(
            request_seller_id=seller.pk,
            lifecycle_status='registered',
            status='registered',
            reviewed_at=now,
            checked_at=now,
            updated_at=now,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0066_drop_registered_seller_public_audit_state'),
    ]

    operations = [
        migrations.RunPython(
            prepare_second_invite_batch,
            migrations.RunPython.noop,
        ),
    ]
