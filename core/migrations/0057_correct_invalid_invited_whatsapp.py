from django.db import migrations
from django.utils import timezone


INVALID_CONTACTS = {
    4: '77002510460',
    19: '77750964795',
    43: '77606225583',
}


def correct_invalid_invited_contacts(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    SellerLeadContactCandidate = apps.get_model('core', 'SellerLeadContactCandidate')
    SellerLeadEvidence = apps.get_model('core', 'SellerLeadEvidence')

    now = timezone.now()

    for lead_id, whatsapp in INVALID_CONTACTS.items():
        lead = SellerLead.objects.filter(
            pk=lead_id,
            whatsapp=whatsapp,
            duplicate_of__isnull=True,
            lifecycle_status='invited',
        ).first()
        if lead is None:
            continue

        note = (
            f'WhatsApp {whatsapp}: номер не зарегистрирован, '
            'ручная проверка 08.10.2026.'
        )

        SellerLeadContactCandidate.objects.filter(
            seller_lead_id=lead_id,
            contact_type='whatsapp',
            value=whatsapp,
        ).update(
            status='rejected',
            is_primary=False,
            reviewed_at=now,
            notes=note,
            updated_at=now,
        )

        SellerLeadEvidence.objects.filter(
            seller_lead_id=lead_id,
            field_name='whatsapp',
            is_selected=True,
        ).filter(
            normalized_value=whatsapp,
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
        lead.marketplace_invitation_status = ''
        lead.marketplace_invitation_planned_at = None
        lead.next_enrichment_at = now
        lead.reviewed_at = now
        lead.review_status = (
            'converted_requests'
            if lead.request_seller_id
            else 'needs_review'
        )
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
            'marketplace_invitation_status',
            'marketplace_invitation_planned_at',
            'next_enrichment_at',
            'reviewed_at',
            'review_status',
            'updated_at',
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0056_mark_sayman_whatsapp_unavailable'),
    ]

    operations = [
        migrations.RunPython(
            correct_invalid_invited_contacts,
            migrations.RunPython.noop,
        ),
    ]
