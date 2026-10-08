from django.db import migrations
from django.utils import timezone


LEAD_ID = 117
WHATSAPP = '77760038662'


def mark_sayman_whatsapp_unavailable(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    SellerLeadContactCandidate = apps.get_model('core', 'SellerLeadContactCandidate')
    SellerLeadEvidence = apps.get_model('core', 'SellerLeadEvidence')

    now = timezone.now()
    lead = SellerLead.objects.filter(
        pk=LEAD_ID,
        whatsapp=WHATSAPP,
        duplicate_of__isnull=True,
        lifecycle_status='ready_to_invite',
    ).first()
    if lead is None:
        return

    note = (
        f'WhatsApp {WHATSAPP}: номер не зарегистрирован, '
        'ручная проверка 08.10.2026.'
    )

    SellerLeadContactCandidate.objects.filter(
        seller_lead_id=LEAD_ID,
        contact_type='whatsapp',
        value=WHATSAPP,
    ).update(
        status='rejected',
        is_primary=False,
        reviewed_at=now,
        notes=note,
        updated_at=now,
    )

    SellerLeadEvidence.objects.filter(
        seller_lead_id=LEAD_ID,
        field_name='whatsapp',
        is_selected=True,
    ).filter(
        normalized_value=WHATSAPP,
    ).update(
        is_selected=False,
        updated_at=now,
    )

    existing_notes = (lead.notes or '').strip()
    lead.notes = f'{existing_notes}\n{note}'.strip() if existing_notes else note
    lead.whatsapp = ''
    lead.whatsapp_confidence = ''
    lead.whatsapp_source_url = ''
    lead.whatsapp_source_text = ''
    lead.whatsapp_found_at = None
    lead.normalized_phone = ''
    lead.status = 'no_whatsapp'
    lead.lifecycle_status = 'classified'
    lead.next_enrichment_at = now
    lead.reviewed_at = now
    lead.updated_at = now
    lead.save(update_fields=[
        'notes',
        'whatsapp',
        'whatsapp_confidence',
        'whatsapp_source_url',
        'whatsapp_source_text',
        'whatsapp_found_at',
        'normalized_phone',
        'status',
        'lifecycle_status',
        'next_enrichment_at',
        'reviewed_at',
        'updated_at',
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0055_prepare_initial_seller_invite_queue'),
    ]

    operations = [
        migrations.RunPython(
            mark_sayman_whatsapp_unavailable,
            migrations.RunPython.noop,
        ),
    ]
