"""Duplicate scoring for Seller Discovery.

Package 1 never deletes a SellerLead, never copies data between leads, and
never sets duplicate_of except through confirm_seller_lead_duplicate().
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import SellerLead, SellerLeadDuplicateMatch
from core.services.seller_discovery_identity import (
    LIFECYCLE_DUPLICATE,
    LIFECYCLE_POSSIBLE_DUPLICATE,
    MUTABLE_LIFECYCLE_FOR_DUPLICATE_REVIEW,
    normalize_address,
    normalize_domain,
    normalize_instagram_identity,
    normalize_seller_name,
    normalize_seller_phone,
    refresh_seller_lead_identity,
)

STRONG_REASON_CODES = frozenset({'external_id', 'instagram', 'phone', 'domain'})
MIN_MATCH_SCORE = 50
PHONE_EVIDENCE_FIELDS = ('phone', 'whatsapp')
INSTAGRAM_EVIDENCE_FIELDS = ('instagram',)
DOMAIN_EVIDENCE_FIELDS = ('website', 'domain')
ADDRESS_EVIDENCE_FIELDS = ('address',)


class SellerDiscoveryDedupError(ValueError):
    pass


@dataclass(frozen=True)
class SellerLeadPairScore:
    score: int
    reasons: list[str]
    strong: bool


def score_seller_lead_pair(lead_a: SellerLead, lead_b: SellerLead) -> SellerLeadPairScore | None:
    if not lead_a.pk or not lead_b.pk or lead_a.pk == lead_b.pk:
        return None

    reasons: list[str] = []
    score = 0

    if _external_keys(lead_a) & _external_keys(lead_b):
        score += 100
        reasons.append('external_id')

    instagram_a = _instagram_values(lead_a)
    instagram_b = _instagram_values(lead_b)
    if instagram_a and instagram_b and (instagram_a & instagram_b):
        score += 95
        reasons.append('instagram')
    elif instagram_a and instagram_b:
        reasons.append('instagram_conflict')

    phones_a = _phone_values(lead_a)
    phones_b = _phone_values(lead_b)
    if phones_a and phones_b and (phones_a & phones_b):
        score += 90
        reasons.append('phone')
    elif phones_a and phones_b:
        reasons.append('phone_conflict')

    domains_a = _domain_values(lead_a)
    domains_b = _domain_values(lead_b)
    if domains_a and domains_b and (domains_a & domains_b):
        score += 80
        reasons.append('domain')
    elif domains_a and domains_b:
        reasons.append('domain_conflict')

    addresses_a = _address_values(lead_a)
    addresses_b = _address_values(lead_b)
    if addresses_a and addresses_b and (addresses_a & addresses_b):
        score += 50
        reasons.append('address')

    name_points = _name_similarity_points(
        lead_a.normalized_name or normalize_seller_name(lead_a.name),
        lead_b.normalized_name or normalize_seller_name(lead_b.name),
    )
    if name_points:
        score += name_points
        reasons.append('name')

    city_a = str(lead_a.city or '').strip().casefold()
    city_b = str(lead_b.city or '').strip().casefold()
    if city_a and city_a == city_b:
        score += 10
        reasons.append('city')

    strong = any(code in STRONG_REASON_CODES for code in reasons)
    return SellerLeadPairScore(score=min(score, 100), reasons=reasons, strong=strong)


def find_possible_duplicates_for_leads(leads) -> list[SellerLeadDuplicateMatch]:
    """Score candidates and store POSSIBLE matches. Never confirms or merges."""
    selected = []
    seen_ids: set[int] = set()
    for lead in leads:
        if lead.pk in seen_ids:
            continue
        seen_ids.add(lead.pk)
        refresh_seller_lead_identity(lead)
        selected.append(lead)

    matches: list[SellerLeadDuplicateMatch] = []
    seen_pairs: set[tuple[int, int]] = set()
    selected_ids = [lead.pk for lead in selected]
    for lead in selected:
        for candidate in _candidate_leads(lead, selected_ids):
            ordered = _ordered_ids(lead.pk, candidate.pk)
            if ordered is None or ordered in seen_pairs:
                continue
            seen_pairs.add(ordered)
            match = _upsert_possible_match(lead, candidate)
            if match is not None:
                matches.append(match)
    return matches


def confirm_seller_lead_duplicate(match: SellerLeadDuplicateMatch, *, canonical_lead, resolved_by):
    """Confirm one explicit direction. Does not delete either lead or copy fields.

    canonical_lead must be one of the two cards in the match. The other card
    becomes the duplicate. The canonical card is not rewritten.
    """
    if resolved_by is None or not getattr(resolved_by, 'pk', None):
        raise SellerDiscoveryDedupError('Нужно указать администратора, который подтверждает дубль.')
    canonical_id = canonical_lead.pk if hasattr(canonical_lead, 'pk') else canonical_lead
    if not canonical_id:
        raise SellerDiscoveryDedupError('Нужно явно выбрать каноническую карточку.')

    with transaction.atomic():
        locked = SellerLeadDuplicateMatch.objects.select_for_update().get(pk=match.pk)
        if canonical_id not in (locked.lead_a_id, locked.lead_b_id):
            raise SellerDiscoveryDedupError('Каноническая карточка не входит в эту пару.')
        duplicate_id = locked.lead_b_id if canonical_id == locked.lead_a_id else locked.lead_a_id
        canonical = SellerLead.objects.select_for_update().get(pk=canonical_id)
        duplicate = SellerLead.objects.select_for_update().get(pk=duplicate_id)
        if canonical.duplicate_of_id or canonical.lifecycle_status == LIFECYCLE_DUPLICATE:
            raise SellerDiscoveryDedupError(
                'Канонической нельзя назначить карточку, которая уже отмечена дублем.',
            )
        if canonical.duplicate_of_id == duplicate.pk:
            raise SellerDiscoveryDedupError(
                'Канонический лид уже отмечен дублем второй карточки.',
            )
        now = timezone.now()
        duplicate.duplicate_of = canonical
        duplicate.lifecycle_status = LIFECYCLE_DUPLICATE
        duplicate.save(update_fields=['duplicate_of', 'lifecycle_status', 'updated_at'])
        locked.status = SellerLeadDuplicateMatch.STATUS_CONFIRMED
        locked.resolved_at = now
        locked.resolved_by = resolved_by
        locked.save(update_fields=['status', 'resolved_at', 'resolved_by', 'updated_at'])
        canonical.refresh_from_db()
        return locked


def reject_seller_lead_duplicate(match: SellerLeadDuplicateMatch, *, resolved_by=None):
    """Mark the pair as reviewed and not a duplicate. Lifecycle is left unchanged."""
    with transaction.atomic():
        locked = SellerLeadDuplicateMatch.objects.select_for_update().get(pk=match.pk)
        locked.status = SellerLeadDuplicateMatch.STATUS_REJECTED
        locked.resolved_at = timezone.now()
        locked.resolved_by = resolved_by
        locked.save(update_fields=['status', 'resolved_at', 'resolved_by', 'updated_at'])
        return locked


def _upsert_possible_match(left: SellerLead, right: SellerLead) -> SellerLeadDuplicateMatch | None:
    low_id, high_id = _ordered_ids(left.pk, right.pk)
    low = left if left.pk == low_id else right
    high = right if right.pk == high_id else left
    scored = score_seller_lead_pair(low, high)
    if scored is None or not _should_store(scored):
        return None

    with transaction.atomic():
        match = SellerLeadDuplicateMatch.objects.select_for_update().filter(
            lead_a_id=low_id,
            lead_b_id=high_id,
        ).first()
        if match is None:
            match = SellerLeadDuplicateMatch.objects.create(
                lead_a_id=low_id,
                lead_b_id=high_id,
                score=scored.score,
                status=SellerLeadDuplicateMatch.STATUS_POSSIBLE,
                reasons=list(scored.reasons),
            )
        elif match.status == SellerLeadDuplicateMatch.STATUS_POSSIBLE:
            match.score = scored.score
            match.reasons = list(scored.reasons)
            match.save(update_fields=['score', 'reasons', 'updated_at'])
        else:
            return match

        if scored.strong:
            _mark_possible_duplicate(low)
            _mark_possible_duplicate(high)
        return match


def _should_store(scored: SellerLeadPairScore) -> bool:
    return scored.strong or scored.score >= MIN_MATCH_SCORE


def _mark_possible_duplicate(lead: SellerLead) -> None:
    if lead.lifecycle_status not in MUTABLE_LIFECYCLE_FOR_DUPLICATE_REVIEW:
        return
    lead.lifecycle_status = LIFECYCLE_POSSIBLE_DUPLICATE
    lead.save(update_fields=['lifecycle_status', 'updated_at'])


def _ordered_ids(left_id: int, right_id: int) -> tuple[int, int] | None:
    if left_id == right_id:
        return None
    if left_id < right_id:
        return left_id, right_id
    return right_id, left_id


def _candidate_leads(lead: SellerLead, selected_ids: list[int]):
    phones = _phone_values(lead)
    instagrams = _instagram_values(lead)
    domains = _domain_values(lead)
    external = _external_keys(lead)
    filters = Q()
    if phones:
        filters |= Q(normalized_phone__in=phones)
        filters |= Q(whatsapp__in=phones)
        filters |= Q(
            evidences__field_name__in=PHONE_EVIDENCE_FIELDS,
            evidences__normalized_value__in=phones,
        )
        filters |= Q(contact_candidates__value__in=phones)
    if instagrams:
        filters |= Q(normalized_instagram__in=instagrams)
        filters |= Q(
            evidences__field_name__in=INSTAGRAM_EVIDENCE_FIELDS,
            evidences__normalized_value__in=instagrams,
        )
    if domains:
        filters |= Q(normalized_domain__in=domains)
        filters |= Q(
            evidences__field_name__in=DOMAIN_EVIDENCE_FIELDS,
            evidences__normalized_value__in=domains,
        )
    if lead.normalized_address:
        filters |= Q(normalized_address=lead.normalized_address)
        filters |= Q(locations__normalized_address=lead.normalized_address)
    if lead.normalized_name:
        filters |= Q(normalized_name=lead.normalized_name)
    for provider, external_id in external:
        filters |= Q(sources__provider=provider, sources__external_id=external_id)
    other_ids = [pk for pk in selected_ids if pk != lead.pk]
    if other_ids:
        filters |= Q(pk__in=other_ids)
    if not filters:
        return []
    return (
        SellerLead.objects.filter(filters)
        .exclude(pk=lead.pk)
        .distinct()
        .prefetch_related('sources', 'evidences', 'contact_candidates', 'locations')
    )


def _name_similarity_points(left: str, right: str) -> int:
    if not left or not right:
        return 0
    if left == right:
        return 30
    ratio = SequenceMatcher(None, left, right).ratio()
    if ratio >= 0.92:
        return 30
    if ratio >= 0.8:
        return 20
    if ratio >= 0.65:
        return 10
    return 0


def _phone_values(lead: SellerLead) -> set[str]:
    values: set[str] = set()
    if lead.normalized_phone:
        values.add(lead.normalized_phone)
    direct = normalize_seller_phone(lead.whatsapp)
    if direct:
        values.add(direct)
    for evidence in lead.evidences.all():
        if evidence.field_name in PHONE_EVIDENCE_FIELDS and evidence.normalized_value:
            values.add(evidence.normalized_value)
    for candidate in lead.contact_candidates.all():
        if candidate.status == candidate.STATUS_REJECTED:
            continue
        phone = normalize_seller_phone(candidate.value)
        if phone:
            values.add(phone)
    return values


def _instagram_values(lead: SellerLead) -> set[str]:
    values: set[str] = set()
    if lead.normalized_instagram:
        values.add(lead.normalized_instagram)
    for raw in (lead.instagram_username, lead.instagram_url):
        normalized = normalize_instagram_identity(raw)
        if normalized:
            values.add(normalized)
    for evidence in lead.evidences.all():
        if evidence.field_name in INSTAGRAM_EVIDENCE_FIELDS and evidence.normalized_value:
            values.add(evidence.normalized_value)
    return values


def _domain_values(lead: SellerLead) -> set[str]:
    values: set[str] = set()
    if lead.normalized_domain:
        values.add(lead.normalized_domain)
    normalized = normalize_domain(lead.website_url)
    if normalized:
        values.add(normalized)
    for evidence in lead.evidences.all():
        if evidence.field_name in DOMAIN_EVIDENCE_FIELDS and evidence.normalized_value:
            values.add(evidence.normalized_value)
    return values


def _address_values(lead: SellerLead) -> set[str]:
    values: set[str] = set()
    if lead.normalized_address:
        values.add(lead.normalized_address)
    for location in lead.locations.all():
        normalized = location.normalized_address or normalize_address(location.address)
        if normalized:
            values.add(normalized)
    for evidence in lead.evidences.all():
        if evidence.field_name in ADDRESS_EVIDENCE_FIELDS and evidence.normalized_value:
            values.add(evidence.normalized_value)
    return values


def _external_keys(lead: SellerLead) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for source in lead.sources.all():
        provider = str(source.provider or '').strip()
        external_id = str(source.external_id or '').strip()
        if provider and external_id:
            keys.add((provider, external_id))
    return keys
