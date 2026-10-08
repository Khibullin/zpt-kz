from datetime import timedelta

from django.db import migrations
from django.utils import timezone


LEAD_ID = 57
INSTAGRAM = 'almaty_parts.kz'
WEBSITE = 'https://almaty-parts.kz/'
DOMAIN = 'almaty-parts.kz'
WHATSAPP = '77474175577'
SOURCE_TEXT = 'Almaty-Parts.kz: WhatsApp https://wa.me/77474175577; Instagram almaty_parts.kz'


def enrich_almaty_parts(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    SellerLeadSource = apps.get_model('core', 'SellerLeadSource')
    SellerLeadEvidence = apps.get_model('core', 'SellerLeadEvidence')
    SellerLeadContactCandidate = apps.get_model('core', 'SellerLeadContactCandidate')

    lead = SellerLead.objects.filter(
        pk=LEAD_ID,
        duplicate_of__isnull=True,
        instagram_username=INSTAGRAM,
    ).first()
    if lead is None:
        return
    if lead.whatsapp and lead.whatsapp != WHATSAPP:
        return
    if lead.website_url and DOMAIN not in lead.website_url.lower():
        return

    now = timezone.now()

    source = SellerLeadSource.objects.filter(
        seller_lead_id=LEAD_ID,
        source_url=WEBSITE,
    ).first()
    if source is None:
        source = SellerLeadSource.objects.create(
            seller_lead_id=LEAD_ID,
            source_type='website',
            provider='website',
            external_id='',
            source_url=WEBSITE,
            display_name='Almaty-Parts.kz',
            first_seen_at=now,
            last_seen_at=now,
            fetched_at=now,
            source_confidence=100,
            is_active=True,
            metadata={'role': 'official_website', 'verified_by': 'manual_review_2026-10-08'},
            raw_payload_hash='',
        )
    else:
        source.source_type = 'website'
        source.provider = 'website'
        source.display_name = 'Almaty-Parts.kz'
        source.last_seen_at = now
        source.fetched_at = now
        source.source_confidence = 100
        source.is_active = True
        source.save(update_fields=[
            'source_type',
            'provider',
            'display_name',
            'last_seen_at',
            'fetched_at',
            'source_confidence',
            'is_active',
            'updated_at',
        ])

    SellerLeadEvidence.objects.filter(
        seller_lead_id=LEAD_ID,
        field_name='whatsapp',
        is_selected=True,
    ).update(is_selected=False, updated_at=now)

    whatsapp_evidence = SellerLeadEvidence.objects.filter(
        seller_lead_id=LEAD_ID,
        field_name='whatsapp',
        normalized_value=WHATSAPP,
    ).order_by('-id').first()
    if whatsapp_evidence is None:
        SellerLeadEvidence.objects.create(
            seller_lead_id=LEAD_ID,
            source_id=source.pk,
            field_name='whatsapp',
            value=WHATSAPP,
            normalized_value=WHATSAPP,
            confidence=100,
            extraction_method='parser',
            observed_at=now,
            is_selected=True,
            is_owner_verified=False,
        )
    else:
        whatsapp_evidence.source_id = source.pk
        whatsapp_evidence.value = WHATSAPP
        whatsapp_evidence.confidence = 100
        whatsapp_evidence.observed_at = now
        whatsapp_evidence.is_selected = True
        whatsapp_evidence.save(update_fields=[
            'source',
            'value',
            'confidence',
            'observed_at',
            'is_selected',
            'updated_at',
        ])

    website_evidence = SellerLeadEvidence.objects.filter(
        seller_lead_id=LEAD_ID,
        field_name='website',
        normalized_value=DOMAIN,
    ).order_by('-id').first()
    if website_evidence is None:
        SellerLeadEvidence.objects.create(
            seller_lead_id=LEAD_ID,
            source_id=source.pk,
            field_name='website',
            value=WEBSITE,
            normalized_value=DOMAIN,
            confidence=100,
            extraction_method='search_result',
            observed_at=now,
            is_selected=True,
            is_owner_verified=False,
        )
    else:
        website_evidence.source_id = source.pk
        website_evidence.value = WEBSITE
        website_evidence.confidence = 100
        website_evidence.observed_at = now
        website_evidence.is_selected = True
        website_evidence.save(update_fields=[
            'source',
            'value',
            'confidence',
            'observed_at',
            'is_selected',
            'updated_at',
        ])

    candidate = SellerLeadContactCandidate.objects.filter(
        seller_lead_id=LEAD_ID,
        contact_type='whatsapp',
        value=WHATSAPP,
    ).first()
    if candidate is None:
        SellerLeadContactCandidate.objects.create(
            seller_lead_id=LEAD_ID,
            contact_type='whatsapp',
            value=WHATSAPP,
            role='shop',
            label='Almaty-Parts.kz WhatsApp',
            confidence='high',
            source_url=WEBSITE,
            source_text=SOURCE_TEXT,
            source_type='wa_me',
            status='pending',
            is_primary=False,
            found_at=now,
        )
    else:
        candidate.role = 'shop'
        candidate.label = 'Almaty-Parts.kz WhatsApp'
        candidate.confidence = 'high'
        candidate.source_url = WEBSITE
        candidate.source_text = SOURCE_TEXT
        candidate.source_type = 'wa_me'
        if candidate.status != 'rejected':
            candidate.status = 'pending'
        candidate.save(update_fields=[
            'role',
            'label',
            'confidence',
            'source_url',
            'source_text',
            'source_type',
            'status',
            'updated_at',
        ])

    lead.website_url = WEBSITE
    lead.normalized_domain = DOMAIN
    lead.whatsapp = WHATSAPP
    lead.normalized_phone = WHATSAPP
    lead.whatsapp_confidence = 'high'
    lead.whatsapp_source_url = WEBSITE
    lead.whatsapp_source_text = SOURCE_TEXT
    lead.whatsapp_found_at = now
    lead.last_enrichment_result = 'verified_whatsapp'
    lead.last_enrichment_attempt_at = now
    lead.next_enrichment_at = now + timedelta(days=90)
    lead.checked_at = now
    lead.updated_at = now
    lead.save(update_fields=[
        'website_url',
        'normalized_domain',
        'whatsapp',
        'normalized_phone',
        'whatsapp_confidence',
        'whatsapp_source_url',
        'whatsapp_source_text',
        'whatsapp_found_at',
        'last_enrichment_result',
        'last_enrichment_attempt_at',
        'next_enrichment_at',
        'checked_at',
        'updated_at',
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0058_activate_invited_sellers_for_requests'),
    ]

    operations = [
        migrations.RunPython(
            enrich_almaty_parts,
            migrations.RunPython.noop,
        ),
    ]
