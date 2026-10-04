"""Computed WhatsApp state for a SellerLead.

The state is not stored. Discovery does not require WhatsApp.
Admin lists read annotated flags instead of querying once per row.
"""

from django.db.models import BooleanField, Exists, ExpressionWrapper, F, OuterRef, Q

from core.models import SellerLead, SellerLeadContactCandidate, SellerLeadEvidence

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

_ANNOTATION_ATTRS = (
    'has_whatsapp_conflict',
    'has_verified_whatsapp',
    'has_pending_whatsapp',
)


def annotate_seller_leads_with_whatsapp_state(queryset):
    """Add conflict, verified, and pending flags with correlated EXISTS."""
    conflict = SellerLeadContactCandidate.objects.filter(
        seller_lead_id=OuterRef('pk'),
        contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        status=SellerLeadContactCandidate.STATUS_CONFLICT,
    )
    verified = SellerLeadEvidence.objects.filter(
        seller_lead_id=OuterRef('pk'),
        field_name='whatsapp',
        is_selected=True,
    ).filter(
        Q(normalized_value=OuterRef('whatsapp')) | Q(value=OuterRef('whatsapp')),
    )
    approved_primary = SellerLeadContactCandidate.objects.filter(
        seller_lead_id=OuterRef('pk'),
        contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        status=SellerLeadContactCandidate.STATUS_APPROVED,
        is_primary=True,
        value=OuterRef('whatsapp'),
    )
    pending = SellerLeadContactCandidate.objects.filter(
        seller_lead_id=OuterRef('pk'),
        contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        status=SellerLeadContactCandidate.STATUS_PENDING,
    )
    has_verified_whatsapp = ExpressionWrapper(
        Exists(verified) | Exists(approved_primary),
        output_field=BooleanField(),
    )
    return queryset.annotate(
        has_whatsapp_conflict=Exists(conflict),
        has_verified_whatsapp=has_verified_whatsapp,
        has_pending_whatsapp=Exists(pending),
    )


def whatsapp_state_from_annotations(lead) -> str:
    """CONFLICT, then VERIFIED, then PENDING, otherwise NOT_FOUND."""
    if lead.has_whatsapp_conflict:
        return WHATSAPP_CONFLICT
    if (lead.whatsapp or '') and lead.has_verified_whatsapp:
        return WHATSAPP_VERIFIED
    if (lead.whatsapp or '') or lead.has_pending_whatsapp:
        return WHATSAPP_PENDING
    return WHATSAPP_NOT_FOUND


def filter_seller_leads_by_whatsapp_state(queryset, state: str):
    annotated = annotate_seller_leads_with_whatsapp_state(queryset)
    if state == WHATSAPP_CONFLICT:
        return annotated.filter(has_whatsapp_conflict=True)
    if state == WHATSAPP_VERIFIED:
        return annotated.filter(
            has_whatsapp_conflict=False,
            has_verified_whatsapp=True,
        ).exclude(whatsapp='')
    if state == WHATSAPP_PENDING:
        return annotated.filter(has_whatsapp_conflict=False).exclude(
            has_verified_whatsapp=True,
            whatsapp__gt='',
        ).filter(
            Q(whatsapp__gt='') | Q(has_pending_whatsapp=True),
        )
    if state == WHATSAPP_NOT_FOUND:
        return annotated.filter(
            has_whatsapp_conflict=False,
            has_pending_whatsapp=False,
            whatsapp='',
        )
    return queryset


def seller_lead_whatsapp_state(lead) -> str:
    """Single-lead state. The admin changelist uses annotations instead."""
    if all(hasattr(lead, name) for name in _ANNOTATION_ATTRS):
        return whatsapp_state_from_annotations(lead)
    row = annotate_seller_leads_with_whatsapp_state(
        SellerLead.objects.filter(pk=lead.pk),
    ).first()
    if row is None:
        return WHATSAPP_NOT_FOUND
    return whatsapp_state_from_annotations(row)


def verified_whatsapp_lead_ids():
    """Leads whose canonical WhatsApp matches one selected evidence row."""
    return SellerLeadEvidence.objects.filter(
        field_name='whatsapp',
        is_selected=True,
    ).exclude(
        seller_lead__whatsapp='',
    ).filter(
        Q(normalized_value=F('seller_lead__whatsapp')) | Q(value=F('seller_lead__whatsapp')),
    ).values('seller_lead_id')
