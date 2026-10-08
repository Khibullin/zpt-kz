from django.db import migrations
from django.utils import timezone


LEAD_BUSINESS_TYPE_FIXES = {
    4: 'new_parts',
    6: 'new_parts',
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
    115: 'wholesaler',
    117: 'new_parts',
    121: 'new_parts',
    124: 'new_parts',
}


def tidy_leads(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    DuplicateMatch = apps.get_model('core', 'SellerLeadDuplicateMatch')
    now = timezone.now()

    for lead_id, business_type in LEAD_BUSINESS_TYPE_FIXES.items():
        lead = SellerLead.objects.filter(pk=lead_id).first()
        if lead is None or lead.lifecycle_status == 'rejected':
            continue
        lead.business_type = business_type
        lead.business_type_confidence = max(int(lead.business_type_confidence or 0), 90)
        lead.business_type_evidence = (
            'seller_directory_cleanup_2026_10_08: explicit business identity '
            'from stored public profile / official website'
        )
        lead.last_classified_at = now
        lead.save(update_fields=[
            'business_type',
            'business_type_confidence',
            'business_type_evidence',
            'last_classified_at',
            'updated_at',
        ])

    # The only current possible-duplicate pair has different Instagram identities
    # and different business names. The shared external locator is stale/conflicting,
    # so keep both leads and close the false duplicate review.
    match = DuplicateMatch.objects.filter(
        lead_a_id=35,
        lead_b_id=59,
        status='possible',
    ).first()
    if match is not None:
        match.status = 'rejected'
        match.resolved_at = now
        match.save(update_fields=['status', 'resolved_at', 'updated_at'])
        SellerLead.objects.filter(
            pk__in=(35, 59),
            lifecycle_status='possible_duplicate',
            duplicate_of__isnull=True,
        ).update(lifecycle_status='classified', updated_at=now)


CREATE_AUDIT_STATE = r"""
CREATE TABLE IF NOT EXISTS core_registered_seller_public_audit (
    seller_id bigint PRIMARY KEY REFERENCES core_seller(id) ON DELETE CASCADE,
    status varchar(20) NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    result text NOT NULL DEFAULT '{}',
    error text NOT NULL DEFAULT '',
    started_at timestamptz NULL,
    finished_at timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO core_registered_seller_public_audit (seller_id)
SELECT id
FROM core_seller
WHERE user_id IS NOT NULL
ON CONFLICT (seller_id) DO NOTHING;
"""


DROP_AUDIT_STATE = """
DROP TABLE IF EXISTS core_registered_seller_public_audit;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0060_save_full_whatsapp_recheck'),
    ]

    operations = [
        migrations.RunPython(tidy_leads, migrations.RunPython.noop),
        migrations.RunSQL(CREATE_AUDIT_STATE, reverse_sql=DROP_AUDIT_STATE),
    ]
