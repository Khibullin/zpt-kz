from datetime import timedelta

from django.db import migrations
from django.utils import timezone


AUDIT_LEAD_IDS = (
    1, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 19, 20, 22, 23,
    24, 25, 26, 28, 29, 31, 32, 33, 34, 35, 36, 37, 38, 40, 41, 42, 43,
    44, 46, 47, 48, 49, 50, 52, 53, 54, 55, 56, 58, 59, 60, 62, 64, 65,
    66, 68, 70, 71, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85,
    86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 99, 100, 101, 102, 103,
    104, 105, 106, 107, 109, 110, 111, 112, 113, 114, 116, 117, 118, 119,
    120, 122, 123, 124, 125, 126, 127, 128, 129,
)

# Only contacts with explicit WhatsApp evidence are selected.
CONFIRMED = {
    37: {
        'phone': '77752011121',
        'website': 'https://autopartskaz.kz/',
        'domain': 'autopartskaz.kz',
        'text': 'Официальный сайт AUTOPARTS_KAZ: WhatsApp +7 775 201 11 21; Instagram @autoparts_kaz.',
    },
    113: {
        'phone': '77064305115',
        'website': 'https://autofanat.kz/',
        'domain': 'autofanat.kz',
        'text': 'Официальный сайт AUTOFANAT.KZ: WhatsApp +7 706 430 51 15.',
    },
    114: {
        'phone': '77772053552',
        'website': 'https://vkparts.kz/',
        'domain': 'vkparts.kz',
        'text': 'Официальный сайт VKparts.kz: WhatsApp +7 777 205 35 52.',
    },
    118: {
        'phone': '77477788886',
        'website': 'https://zapchasti.kz/',
        'domain': 'zapchasti.kz',
        'text': 'Официальные контакты zapchasti.kz: отдел запасных частей WhatsApp +7 747 778 88 86.',
    },
    125: {
        'phone': '77074556350',
        'website': 'https://chinapart.kz/',
        'domain': 'chinapart.kz',
        'text': 'Официальный сайт China Part: WhatsApp wa.me/77074556350.',
    },
    126: {
        'phone': '77759772338',
        'website': 'https://autoleader.kz/',
        'domain': 'autoleader.kz',
        'text': 'Официальный сайт AUTOLEADER: Whats App +7 775 977 23 38.',
    },
}

# Explicit WhatsApp is present, but several numbers are exposed and the site
# does not provide a safe canonical choice. Keep as a conflict for review.
CONFLICTS = {
    119: {
        'website': 'https://www.autobak.kz/',
        'domain': 'autobak.kz',
        'phones': ('77773946644', '77072179696'),
        'text': 'Официальный сайт Autobak: несколько WhatsApp-контактов; требуется выбор основного.',
    },
}

# WhatsApp presence was confirmed during the audit, but the public result did
# not expose an unambiguous number-to-WhatsApp mapping. Do not guess.
AMBIGUOUS_PRESENCE = (60, 79, 82, 90, 116)


def _website_source(SellerLeadSource, lead_id, website, now):
    # Source URL is unique per lead regardless of source_type. Reuse an
    # existing Brave/web-search row instead of inserting a duplicate.
    source = SellerLeadSource.objects.filter(
        seller_lead_id=lead_id,
        source_url=website,
    ).first()
    if source:
        source.source_type = 'website'
        source.provider = 'website'
        source.last_seen_at = now
        source.fetched_at = now
        source.is_active = True
        source.source_confidence = 100
        source.metadata = {
            'role': 'official_website',
            'verified_by': 'full_whatsapp_recheck_2026-10-08',
        }
        source.save(update_fields=[
            'source_type', 'provider', 'last_seen_at', 'fetched_at',
            'is_active', 'source_confidence', 'metadata', 'updated_at',
        ])
        return source

    return SellerLeadSource.objects.create(
        seller_lead_id=lead_id,
        source_type='website',
        provider='website',
        external_id='',
        source_url=website,
        display_name='',
        first_seen_at=now,
        last_seen_at=now,
        fetched_at=now,
        source_confidence=100,
        is_active=True,
        metadata={'role': 'official_website', 'verified_by': 'full_whatsapp_recheck_2026-10-08'},
        raw_payload_hash='',
    )


def _store_evidence(
    SellerLeadEvidence,
    *,
    lead_id,
    source_id,
    phone,
    confidence,
    selected,
    now,
):
    if selected:
        SellerLeadEvidence.objects.filter(
            seller_lead_id=lead_id,
            field_name='whatsapp',
            is_selected=True,
        ).update(is_selected=False, updated_at=now)

    row = SellerLeadEvidence.objects.filter(
        seller_lead_id=lead_id,
        field_name='whatsapp',
        normalized_value=phone,
    ).order_by('-id').first()
    if row is None:
        SellerLeadEvidence.objects.create(
            seller_lead_id=lead_id,
            source_id=source_id,
            field_name='whatsapp',
            value=phone,
            normalized_value=phone,
            confidence=confidence,
            extraction_method='manual',
            observed_at=now,
            is_selected=selected,
            is_owner_verified=False,
        )
        return
    row.source_id = source_id
    row.value = phone
    row.confidence = confidence
    row.observed_at = now
    row.is_selected = selected
    row.save(update_fields=[
        'source', 'value', 'confidence', 'observed_at',
        'is_selected', 'updated_at',
    ])


def _store_candidate(
    SellerLeadContactCandidate,
    *,
    lead_id,
    phone,
    website,
    text,
    status,
    now,
):
    row = SellerLeadContactCandidate.objects.filter(
        seller_lead_id=lead_id,
        contact_type='whatsapp',
        value=phone,
    ).first()
    if row is None:
        SellerLeadContactCandidate.objects.create(
            seller_lead_id=lead_id,
            contact_type='whatsapp',
            value=phone,
            role='shop',
            label='',
            confidence='high',
            source_url=website,
            source_text=text[:400],
            source_type='wa_me',
            status=status,
            is_primary=False,
            found_at=now,
        )
        return
    if row.status == 'rejected':
        return
    row.confidence = 'high'
    row.source_url = website
    row.source_text = text[:400]
    row.source_type = 'wa_me'
    row.status = status
    row.is_primary = False
    row.reviewed_at = now if status == 'conflict' else row.reviewed_at
    row.save(update_fields=[
        'confidence', 'source_url', 'source_text', 'source_type',
        'status', 'is_primary', 'reviewed_at', 'updated_at',
    ])


def apply_full_recheck(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    SellerLeadSource = apps.get_model('core', 'SellerLeadSource')
    SellerLeadEvidence = apps.get_model('core', 'SellerLeadEvidence')
    SellerLeadContactCandidate = apps.get_model('core', 'SellerLeadContactCandidate')

    now = timezone.now()
    next_missing = now + timedelta(days=7)
    next_verified = now + timedelta(days=90)

    # Audit stamp: all 108 leads were rechecked, not just the newly found ones.
    SellerLead.objects.filter(
        pk__in=AUDIT_LEAD_IDS,
        duplicate_of__isnull=True,
    ).update(
        checked_at=now,
        last_enrichment_attempt_at=now,
        next_enrichment_at=next_missing,
        updated_at=now,
    )

    for lead_id, item in CONFIRMED.items():
        lead = SellerLead.objects.filter(
            pk=lead_id,
            duplicate_of__isnull=True,
        ).first()
        if lead is None:
            continue
        # Never overwrite a different live/rejected contact found meanwhile.
        if lead.whatsapp and lead.whatsapp != item['phone']:
            continue
        rejected = SellerLeadContactCandidate.objects.filter(
            seller_lead_id=lead_id,
            contact_type='whatsapp',
            value=item['phone'],
            status='rejected',
        ).exists()
        if rejected:
            continue

        source = _website_source(
            SellerLeadSource,
            lead_id,
            item['website'],
            now,
        )
        _store_evidence(
            SellerLeadEvidence,
            lead_id=lead_id,
            source_id=source.pk,
            phone=item['phone'],
            confidence=100,
            selected=True,
            now=now,
        )
        _store_candidate(
            SellerLeadContactCandidate,
            lead_id=lead_id,
            phone=item['phone'],
            website=item['website'],
            text=item['text'],
            status='pending',
            now=now,
        )

        lead.website_url = item['website']
        lead.normalized_domain = item['domain']
        lead.whatsapp = item['phone']
        lead.normalized_phone = item['phone']
        lead.whatsapp_confidence = 'high'
        lead.whatsapp_source_url = item['website']
        lead.whatsapp_source_text = item['text']
        lead.whatsapp_found_at = lead.whatsapp_found_at or now
        lead.last_enrichment_result = 'verified_whatsapp'
        lead.last_enriched_at = now
        lead.next_enrichment_at = next_verified
        lead.checked_at = now
        if lead.lifecycle_status == 'found':
            lead.lifecycle_status = 'enriched'
        if lead.status == 'no_whatsapp':
            lead.status = 'needs_review'
        if lead.market_scope == 'unknown':
            lead.market_scope = 'kz'
            lead.market_scope_evidence = 'Повторная ручная web-проверка: официальный сайт продавца в Казахстане.'
        lead.save(update_fields=[
            'website_url', 'normalized_domain', 'whatsapp', 'normalized_phone',
            'whatsapp_confidence', 'whatsapp_source_url', 'whatsapp_source_text',
            'whatsapp_found_at', 'last_enrichment_result', 'last_enriched_at',
            'next_enrichment_at', 'checked_at', 'lifecycle_status', 'status',
            'market_scope', 'market_scope_evidence', 'updated_at',
        ])

    for lead_id, item in CONFLICTS.items():
        lead = SellerLead.objects.filter(
            pk=lead_id,
            duplicate_of__isnull=True,
            whatsapp='',
        ).first()
        if lead is None:
            continue
        source = _website_source(
            SellerLeadSource,
            lead_id,
            item['website'],
            now,
        )
        for phone in item['phones']:
            rejected = SellerLeadContactCandidate.objects.filter(
                seller_lead_id=lead_id,
                contact_type='whatsapp',
                value=phone,
                status='rejected',
            ).exists()
            if rejected:
                continue
            _store_evidence(
                SellerLeadEvidence,
                lead_id=lead_id,
                source_id=source.pk,
                phone=phone,
                confidence=95,
                selected=False,
                now=now,
            )
            _store_candidate(
                SellerLeadContactCandidate,
                lead_id=lead_id,
                phone=phone,
                website=item['website'],
                text=item['text'],
                status='conflict',
                now=now,
            )
        lead.website_url = item['website']
        lead.normalized_domain = item['domain']
        lead.last_enrichment_result = 'conflict'
        lead.checked_at = now
        lead.next_enrichment_at = next_missing
        lead.save(update_fields=[
            'website_url', 'normalized_domain', 'last_enrichment_result',
            'checked_at', 'next_enrichment_at', 'updated_at',
        ])

    SellerLead.objects.filter(
        pk__in=AMBIGUOUS_PRESENCE,
        duplicate_of__isnull=True,
        whatsapp='',
    ).update(
        last_enrichment_result='ambiguous',
        checked_at=now,
        next_enrichment_at=next_missing,
        updated_at=now,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0059_enrich_almaty_parts_contact'),
    ]

    operations = [
        migrations.RunPython(
            apply_full_recheck,
            migrations.RunPython.noop,
        ),
    ]
