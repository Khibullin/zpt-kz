from __future__ import annotations

from django.db.models import Q

from marketing.services.campaigns.constants import PURPOSE_COMPATIBILITY


def is_purpose_audience_compatible(
    purpose: str,
    *,
    contact_group: str,
    contact_subtype: str,
) -> bool:
    allowed = PURPOSE_COMPATIBILITY.get(purpose)
    if not allowed:
        return False
    return (contact_group, contact_subtype) in allowed


def is_audience_compatible_with_purpose(audience, purpose: str) -> bool:
    return is_purpose_audience_compatible(
        purpose,
        contact_group=audience.contact_group,
        contact_subtype=audience.contact_subtype,
    )


def compatible_audiences_for_purpose(purpose: str):
    from marketing.models import MarketingAudience

    allowed = PURPOSE_COMPATIBILITY.get(purpose, frozenset())
    if not allowed:
        return MarketingAudience.objects.none()
    query = Q()
    for contact_group, contact_subtype in allowed:
        query |= Q(contact_group=contact_group, contact_subtype=contact_subtype)
    return MarketingAudience.objects.filter(query, is_active=True).order_by('name')
