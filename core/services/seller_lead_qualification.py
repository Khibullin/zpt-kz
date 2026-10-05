"""Operational qualification derived from lifecycle, market, and business type.

This is not a second status field. Duplicate and rejected stay on lifecycle_status.
"""

from __future__ import annotations

from core.models import (
    BUSINESS_TYPE_DEALER,
    BUSINESS_TYPE_DISMANTLER,
    BUSINESS_TYPE_MIXED,
    BUSINESS_TYPE_NEW_PARTS,
    BUSINESS_TYPE_OTHER_AUTO,
    BUSINESS_TYPE_SERVICE_ONLY,
    BUSINESS_TYPE_SERVICE_PARTS,
    BUSINESS_TYPE_UNKNOWN,
    BUSINESS_TYPE_WHOLESALER,
    MARKET_SCOPE_FOREIGN,
    MARKET_SCOPE_KZ,
    SellerLead,
)

QUALIFICATION_PENDING = 'pending'
QUALIFICATION_QUALIFIED = 'qualified'
QUALIFICATION_NEEDS_REVIEW = 'needs_review'
QUALIFICATION_REJECTED = 'rejected'
QUALIFICATION_DUPLICATE = 'duplicate'
QUALIFICATION_FOREIGN = 'foreign'

TARGET_BUSINESS_TYPES = frozenset({
    BUSINESS_TYPE_NEW_PARTS,
    BUSINESS_TYPE_DISMANTLER,
    BUSINESS_TYPE_WHOLESALER,
    BUSINESS_TYPE_DEALER,
    BUSINESS_TYPE_SERVICE_PARTS,
    BUSINESS_TYPE_MIXED,
})
REVIEW_BUSINESS_TYPES = frozenset({
    BUSINESS_TYPE_SERVICE_ONLY,
    BUSINESS_TYPE_OTHER_AUTO,
    BUSINESS_TYPE_UNKNOWN,
})
BLOCKED_LIFECYCLES = frozenset({
    SellerLead.LIFECYCLE_DUPLICATE,
    SellerLead.LIFECYCLE_REJECTED,
    SellerLead.LIFECYCLE_CLOSED,
})


def qualification_status(lead: SellerLead) -> str:
    if lead.lifecycle_status == SellerLead.LIFECYCLE_DUPLICATE or lead.duplicate_of_id:
        return QUALIFICATION_DUPLICATE
    if lead.lifecycle_status in {SellerLead.LIFECYCLE_REJECTED, SellerLead.LIFECYCLE_CLOSED}:
        return QUALIFICATION_REJECTED
    if lead.market_scope == MARKET_SCOPE_FOREIGN:
        return QUALIFICATION_FOREIGN
    if lead.business_type in REVIEW_BUSINESS_TYPES or lead.market_scope != MARKET_SCOPE_KZ:
        if lead.last_classified_at is None and lead.business_type == BUSINESS_TYPE_UNKNOWN:
            return QUALIFICATION_PENDING
        if lead.business_type in {BUSINESS_TYPE_SERVICE_ONLY, BUSINESS_TYPE_OTHER_AUTO}:
            return QUALIFICATION_NEEDS_REVIEW
        if lead.market_scope != MARKET_SCOPE_KZ or lead.business_type == BUSINESS_TYPE_UNKNOWN:
            return QUALIFICATION_PENDING if lead.last_classified_at is None else QUALIFICATION_NEEDS_REVIEW
    if lead.business_type in TARGET_BUSINESS_TYPES and lead.market_scope == MARKET_SCOPE_KZ:
        return QUALIFICATION_QUALIFIED
    return QUALIFICATION_PENDING


def is_enrichment_target(lead: SellerLead) -> bool:
    """True when a paid/network enrichment pass is worth running."""
    if lead.request_seller_id:
        return False
    if lead.lifecycle_status in BLOCKED_LIFECYCLES or lead.duplicate_of_id:
        return False
    return qualification_status(lead) == QUALIFICATION_QUALIFIED
