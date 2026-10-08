from django.db import migrations
from django.utils import timezone


# Manually reviewed on 2026-10-08 from the first WhatsApp seller cohort.
# The migration only prepares the queue; it never sends WhatsApp messages.
REVIEWED_LEADS = {
    2: ('77768266888', 'wholesaler'),
    3: ('77052288557', 'new_parts'),
    4: ('77002510460', 'new_parts'),
    11: ('77076869554', 'dismantler'),
    18: ('77777434743', 'new_parts'),
    19: ('77750964795', 'dealer'),
    21: ('77066653395', 'dismantler'),
    30: ('77073100013', 'new_parts'),
    43: ('77606225583', 'new_parts'),
    45: ('77719902281', 'dealer'),
    51: ('77776534499', 'wholesaler'),
    63: ('77006891915', 'dismantler'),
    69: ('77717600617', 'dealer'),
    72: ('77056244576', 'new_parts'),
    97: ('77477772277', 'new_parts'),
    98: ('77780189860', 'dealer'),
    108: ('77750308952', 'dealer'),
    115: ('77717569101', 'new_parts'),
    117: ('77760038662', 'new_parts'),
    121: ('77472323290', 'mixed'),
}

PREPARABLE_LIFECYCLES = (
    'found',
    'enriched',
    'classified',
    'ready_to_invite',
)
BLOCKED_LIFECYCLES = (
    'duplicate',
    'rejected',
    'closed',
)


def prepare_initial_invite_queue(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    now = timezone.now()
    business_evidence = 'Ручная проверка: первая WhatsApp-очередь приглашений 08.10.2026'
    market_evidence = 'Ручная проверка: продавец работает на рынке Казахстана'

    for lead_id, (whatsapp, business_type) in REVIEWED_LEADS.items():
        base = SellerLead.objects.filter(
            pk=lead_id,
            whatsapp=whatsapp,
            duplicate_of__isnull=True,
        ).exclude(lifecycle_status__in=BLOCKED_LIFECYCLES)

        # Preserve later funnel states if a seller registers or is invited
        # while this deploy is in progress, but keep the reviewed metadata.
        base.update(
            business_type=business_type,
            business_type_confidence=100,
            business_type_evidence=business_evidence,
            market_scope='kz',
            market_scope_evidence=market_evidence,
            last_classified_at=now,
            checked_at=now,
            reviewed_at=now,
            updated_at=now,
        )
        base.filter(
            lifecycle_status__in=PREPARABLE_LIFECYCLES,
        ).update(
            lifecycle_status='ready_to_invite',
            updated_at=now,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0054_instagrampublication_public_review'),
    ]

    operations = [
        migrations.RunPython(
            prepare_initial_invite_queue,
            migrations.RunPython.noop,
        ),
    ]
