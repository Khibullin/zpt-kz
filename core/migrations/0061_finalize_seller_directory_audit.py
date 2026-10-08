from django.db import migrations
from django.utils import timezone


BUSINESS_TYPE_FIXES = {
    4: 'new_parts',
    18: 'new_parts',
    19: 'dealer',
    25: 'new_parts',
    30: 'new_parts',
    45: 'dealer',
    51: 'new_parts',
    67: 'other_auto',
    71: 'new_parts',
    72: 'new_parts',
    73: 'new_parts',
    83: 'service_only',
    100: 'mixed',
    111: 'new_parts',
    115: 'new_parts',
    117: 'new_parts',
    121: 'mixed',
    124: 'wholesaler',
}


def finalize_seller_directory_audit(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    now = timezone.now()
    evidence = 'seller_directory_audit_2026_10_08: explicit stored/public business identity'

    for lead_id, business_type in BUSINESS_TYPE_FIXES.items():
        SellerLead.objects.filter(
            pk=lead_id,
            business_type='unknown',
        ).update(
            business_type=business_type,
            business_type_confidence=95,
            business_type_evidence=evidence,
            last_classified_at=now,
            updated_at=now,
        )

    # A verified WhatsApp was found during the audit.  Do not leave the
    # pre-audit "no_whatsapp" operational status behind.
    SellerLead.objects.filter(
        pk=4,
        status='no_whatsapp',
    ).exclude(whatsapp='').update(
        status='needs_review',
        updated_at=now,
    )

    # Parts.kz is explicitly an Almaty/Kazakhstan parts shop on a .kz domain.
    # The conservative classifier left it unknown because legacy source
    # evidence was sparse; the audit verified the Kazakhstan identity.
    SellerLead.objects.filter(
        pk=116,
        market_scope='unknown',
    ).update(
        market_scope='kz',
        market_scope_evidence=(
            'seller_directory_audit_2026_10_08: official Parts.kz site, '
            'Almaty identity and Kazakhstan delivery'
        ),
        updated_at=now,
    )


def noop_reverse(apps, schema_editor):
    # Production audit facts are intentionally not guessed backwards.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0060_save_full_whatsapp_recheck'),
    ]

    operations = [
        migrations.RunPython(finalize_seller_directory_audit, noop_reverse),
    ]
