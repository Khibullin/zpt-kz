"""Computed WhatsApp state for a SellerLead.

The state is not stored. Discovery does not require WhatsApp.
"""

from django.db.models import F, Q

from core.models import SellerLeadContactCandidate, SellerLeadEvidence

WHATSAPP_VERIFIED = 'verified'
WHATSAPP_PENDING = 'pending'
WHATSAPP_CONFLICT = 'conflict'
WHATSAPP_NOT_FOUND = 'not_found'

WHATSAPP_STATE_CHOICES = (
    (WHATSAPP_VERIFIED, 'VERIFIED'),
    (WHATSAPP_PENDING, 'PENDING'),
    (WHATSAPP_CONFLICT, 'CONFLICT'),
    (WHATSAPP_NOT_FOUND, 'NOT_FOUND'),
)


def _id_list(queryset):
    return list(queryset.values_list('pk', flat=True).distinct())


def _conflict_ids(queryset):
    return _id_list(queryset.filter(
        contact_candidates__contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        contact_candidates__status=SellerLeadContactCandidate.STATUS_CONFLICT,
    ))


def _verified_ids(queryset, conflict_ids):
    return _id_list(queryset.exclude(pk__in=conflict_ids).exclude(whatsapp='').filter(
        evidences__field_name='whatsapp',
        evidences__is_selected=True,
    ).filter(
        Q(evidences__normalized_value=F('whatsapp')) | Q(evidences__value=F('whatsapp')),
    ))


def _pending_ids(queryset, conflict_ids, verified_ids):
    return _id_list(queryset.exclude(pk__in=conflict_ids).exclude(pk__in=verified_ids).filter(
        Q(whatsapp__gt='')
        | Q(
            contact_candidates__contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            contact_candidates__status=SellerLeadContactCandidate.STATUS_PENDING,
        ),
    ))


def filter_seller_leads_by_whatsapp_state(queryset, state: str):
    conflict_ids = _conflict_ids(queryset)
    verified_ids = _verified_ids(queryset, conflict_ids)
    pending_ids = _pending_ids(queryset, conflict_ids, verified_ids)
    if state == WHATSAPP_CONFLICT:
        return queryset.filter(pk__in=conflict_ids)
    if state == WHATSAPP_VERIFIED:
        return queryset.filter(pk__in=verified_ids)
    if state == WHATSAPP_PENDING:
        return queryset.filter(pk__in=pending_ids)
    if state == WHATSAPP_NOT_FOUND:
        occupied = conflict_ids + verified_ids + pending_ids
        return queryset.exclude(pk__in=occupied)
    return queryset


def seller_lead_whatsapp_state(lead) -> str:
    queryset = lead.__class__.objects.filter(pk=lead.pk)
    if filter_seller_leads_by_whatsapp_state(queryset, WHATSAPP_CONFLICT).exists():
        return WHATSAPP_CONFLICT
    if filter_seller_leads_by_whatsapp_state(queryset, WHATSAPP_VERIFIED).exists():
        return WHATSAPP_VERIFIED
    if filter_seller_leads_by_whatsapp_state(queryset, WHATSAPP_PENDING).exists():
        return WHATSAPP_PENDING
    return WHATSAPP_NOT_FOUND


def verified_whatsapp_lead_ids():
    return SellerLeadEvidence.objects.filter(
        field_name='whatsapp',
        is_selected=True,
    ).exclude(
        seller_lead__whatsapp='',
    ).filter(
        Q(normalized_value=F('seller_lead__whatsapp')) | Q(value=F('seller_lead__whatsapp')),
    ).values('seller_lead_id')
