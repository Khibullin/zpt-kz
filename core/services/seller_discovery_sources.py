"""Idempotent source, evidence, and location writes for Seller Discovery.

Helpers never perform network requests and never create Seller, User,
SellerProfile, or Product records.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import SellerLeadEvidence, SellerLeadLocation, SellerLeadSource
from core.services.seller_discovery_identity import (
    normalize_address,
    normalize_domain,
    normalize_instagram_identity,
    normalize_seller_name,
    normalize_seller_phone,
)

PHONE_FIELDS = frozenset({'phone', 'whatsapp'})
DOMAIN_FIELDS = frozenset({'website', 'domain'})
INSTAGRAM_FIELDS = frozenset({'instagram'})
ADDRESS_FIELDS = frozenset({'address'})
NAME_FIELDS = frozenset({'name', 'seller_name'})


class SellerDiscoverySourceError(ValueError):
    pass


class SellerDiscoveryEvidenceError(ValueError):
    pass


def _validate_confidence(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100:
        raise SellerDiscoverySourceError('Уверенность должна быть целым числом от 0 до 100.')
    return value


def _normalize_evidence_value(field_name: str, value: str) -> str:
    if field_name in PHONE_FIELDS:
        return normalize_seller_phone(value)
    if field_name in DOMAIN_FIELDS:
        return normalize_domain(value)
    if field_name in INSTAGRAM_FIELDS:
        return normalize_instagram_identity(value)
    if field_name in ADDRESS_FIELDS:
        return normalize_address(value)
    if field_name in NAME_FIELDS:
        return normalize_seller_name(value)
    return str(value or '').strip()


def upsert_seller_lead_source(
    seller_lead,
    *,
    source_type: str,
    provider: str = '',
    external_id: str = '',
    source_url: str = '',
    display_name: str = '',
    fetched_at=None,
    source_confidence: int | None = None,
    is_active: bool | None = None,
    metadata: dict | None = None,
    raw_payload_hash: str = '',
    observed_at=None,
) -> SellerLeadSource:
    """Create or refresh one source observation.

    The same external id or the same source URL for one lead does not create
    another row. An external id already stored on a different lead is refused
    instead of being moved.
    """
    provider = str(provider or '').strip()
    external_id = str(external_id or '').strip()
    source_url = str(source_url or '').strip()[:500]
    display_name = str(display_name or '').strip()[:255]
    raw_payload_hash = str(raw_payload_hash or '').strip()[:64]
    confidence = _validate_confidence(source_confidence)
    seen_at = observed_at or timezone.now()

    with transaction.atomic():
        existing = _find_existing_source(
            seller_lead,
            source_type=source_type,
            provider=provider,
            external_id=external_id,
            source_url=source_url,
        )
        if existing is None:
            return SellerLeadSource.objects.create(
                seller_lead=seller_lead,
                source_type=source_type,
                provider=provider,
                external_id=external_id,
                source_url=source_url,
                display_name=display_name,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
                fetched_at=fetched_at,
                source_confidence=confidence,
                is_active=True if is_active is None else is_active,
                metadata=dict(metadata or {}),
                raw_payload_hash=raw_payload_hash,
            )

        if existing.seller_lead_id != seller_lead.pk:
            raise SellerDiscoverySourceError(
                'Этот внешний идентификатор уже сохранён у другого найденного продавца.',
            )

        existing.last_seen_at = seen_at
        update_fields = ['last_seen_at', 'updated_at']
        if display_name:
            existing.display_name = display_name
            update_fields.append('display_name')
        if fetched_at is not None:
            existing.fetched_at = fetched_at
            update_fields.append('fetched_at')
        if confidence is not None:
            existing.source_confidence = confidence
            update_fields.append('source_confidence')
        if is_active is not None:
            existing.is_active = is_active
            update_fields.append('is_active')
        if metadata:
            merged = dict(existing.metadata or {})
            merged.update(metadata)
            existing.metadata = merged
            update_fields.append('metadata')
        if raw_payload_hash:
            existing.raw_payload_hash = raw_payload_hash
            update_fields.append('raw_payload_hash')
        if source_url and source_url != existing.source_url:
            clash = SellerLeadSource.objects.filter(
                seller_lead=seller_lead,
                source_url=source_url,
            ).exclude(pk=existing.pk).exists()
            if not clash:
                existing.source_url = source_url
                update_fields.append('source_url')
        existing.save(update_fields=update_fields)
        return existing


def _find_existing_source(
    seller_lead,
    *,
    source_type: str,
    provider: str,
    external_id: str,
    source_url: str,
) -> SellerLeadSource | None:
    if external_id:
        found = SellerLeadSource.objects.select_for_update().filter(
            source_type=source_type,
            provider=provider,
            external_id=external_id,
        ).first()
        if found is not None:
            return found
    if source_url:
        return SellerLeadSource.objects.select_for_update().filter(
            seller_lead=seller_lead,
            source_url=source_url,
        ).first()
    return SellerLeadSource.objects.select_for_update().filter(
        seller_lead=seller_lead,
        source_type=source_type,
        provider=provider,
        external_id='',
        source_url='',
    ).first()


def add_seller_lead_evidence(
    seller_lead,
    *,
    field_name: str,
    value: str,
    source: SellerLeadSource | None = None,
    normalized_value: str | None = None,
    confidence: int | None = None,
    extraction_method: str = SellerLeadEvidence.METHOD_OTHER,
    observed_at=None,
    is_selected: bool = False,
    is_owner_verified: bool = False,
) -> SellerLeadEvidence:
    field_name = str(field_name or '').strip()[:64]
    if not field_name:
        raise SellerDiscoveryEvidenceError('Имя поля evidence не может быть пустым.')
    stored_value = str(value or '')
    normalized = (
        normalized_value
        if normalized_value is not None
        else _normalize_evidence_value(field_name, stored_value)
    )
    confidence = _validate_confidence(confidence)
    observed = observed_at or timezone.now()

    with transaction.atomic():
        existing = SellerLeadEvidence.objects.select_for_update().filter(
            seller_lead=seller_lead,
            field_name=field_name,
            normalized_value=normalized,
            source=source,
            extraction_method=extraction_method,
        ).first()
        if existing is not None and existing.is_owner_verified and not is_owner_verified:
            if observed > existing.observed_at:
                existing.observed_at = observed
                existing.save(update_fields=['observed_at', 'updated_at'])
            return existing

        if existing is None:
            existing = SellerLeadEvidence(
                seller_lead=seller_lead,
                source=source,
                field_name=field_name,
                value=stored_value,
                normalized_value=normalized,
                confidence=confidence,
                extraction_method=extraction_method,
                observed_at=observed,
                is_selected=False,
                is_owner_verified=is_owner_verified,
            )
            existing.save()
        else:
            existing.value = stored_value
            existing.normalized_value = normalized
            if confidence is not None:
                existing.confidence = confidence
            existing.observed_at = observed
            existing.is_owner_verified = existing.is_owner_verified or is_owner_verified
            existing.save(update_fields=[
                'value',
                'normalized_value',
                'confidence',
                'observed_at',
                'is_owner_verified',
                'updated_at',
            ])

        if is_selected:
            _select_evidence_locked(existing, strict=False)
        return existing


CANDIDATE_CONFIDENCE_TO_EVIDENCE = {
    'high': 100,
    'medium': 80,
    'low': 60,
}


def ensure_selected_whatsapp_evidence(
    seller_lead,
    *,
    value: str,
    confidence: int | None,
    observed_at=None,
) -> SellerLeadEvidence:
    """Select matching WhatsApp evidence, or create one manual row.

    A ZPT admin approval is not owner verification. An existing row with the
    same normalized number is selected instead of inserting a duplicate.
    """
    normalized = _normalize_evidence_value('whatsapp', value)
    with transaction.atomic():
        existing = (
            SellerLeadEvidence.objects.select_for_update()
            .filter(
                seller_lead=seller_lead,
                field_name='whatsapp',
                normalized_value=normalized,
            )
            .order_by('-is_selected', '-pk')
            .first()
        )
        if existing is not None:
            return select_seller_lead_evidence(existing)
        return add_seller_lead_evidence(
            seller_lead,
            field_name='whatsapp',
            value=value,
            normalized_value=normalized,
            confidence=confidence,
            extraction_method=SellerLeadEvidence.METHOD_MANUAL,
            observed_at=observed_at,
            is_selected=True,
            is_owner_verified=False,
        )


def select_seller_lead_evidence(evidence: SellerLeadEvidence) -> SellerLeadEvidence:
    with transaction.atomic():
        locked = SellerLeadEvidence.objects.select_for_update().get(pk=evidence.pk)
        _select_evidence_locked(locked, strict=True)
        return locked


def _select_evidence_locked(evidence: SellerLeadEvidence, *, strict: bool) -> None:
    owner_selected = SellerLeadEvidence.objects.select_for_update().filter(
        seller_lead_id=evidence.seller_lead_id,
        field_name=evidence.field_name,
        is_selected=True,
        is_owner_verified=True,
    ).exclude(pk=evidence.pk).exists()
    if owner_selected and not evidence.is_owner_verified:
        if strict:
            raise SellerDiscoveryEvidenceError(
                'Нельзя заменить подтверждённое владельцем значение внешним источником.',
            )
        return
    SellerLeadEvidence.objects.filter(
        seller_lead_id=evidence.seller_lead_id,
        field_name=evidence.field_name,
        is_selected=True,
    ).exclude(pk=evidence.pk).update(is_selected=False)
    if not evidence.is_selected:
        evidence.is_selected = True
        evidence.save(update_fields=['is_selected', 'updated_at'])


def choose_preferred_evidence(seller_lead, field_name: str) -> SellerLeadEvidence | None:
    """Owner-verified evidence outranks selected external evidence."""
    evidences = list(
        SellerLeadEvidence.objects.filter(
            seller_lead=seller_lead,
            field_name=field_name,
        )
    )
    if not evidences:
        return None

    def sort_key(item: SellerLeadEvidence):
        return (
            1 if item.is_owner_verified else 0,
            1 if item.is_selected else 0,
            item.confidence if item.confidence is not None else -1,
            item.observed_at,
            item.pk or 0,
        )

    return max(evidences, key=sort_key)


def set_primary_location(location: SellerLeadLocation) -> SellerLeadLocation:
    with transaction.atomic():
        locked = SellerLeadLocation.objects.select_for_update().select_related(
            'seller_lead',
        ).get(pk=location.pk)
        SellerLeadLocation.objects.filter(
            seller_lead_id=locked.seller_lead_id,
            is_primary=True,
        ).exclude(pk=locked.pk).update(is_primary=False)
        if not locked.normalized_address:
            locked.normalized_address = normalize_address(locked.address)
        locked.is_primary = True
        locked.save(update_fields=['is_primary', 'normalized_address', 'updated_at'])
        lead = locked.seller_lead
        lead.normalized_address = locked.normalized_address
        lead.save(update_fields=['normalized_address', 'updated_at'])
        return locked


def upsert_seller_lead_location(
    seller_lead,
    *,
    source: SellerLeadSource | None = None,
    external_id: str = '',
    name: str = '',
    city: str = '',
    address: str = '',
    latitude=None,
    longitude=None,
    confidence: int | None = None,
    observed_at=None,
    make_primary_if_missing: bool = True,
) -> SellerLeadLocation | None:
    """Create or refresh one location. Does not replace an existing primary point."""
    external_id = str(external_id or '').strip()[:255]
    name = str(name or '').strip()[:255]
    city = str(city or '').strip()[:100]
    address = str(address or '').strip()[:500]
    normalized = normalize_address(address)
    confidence = _validate_confidence(confidence)
    seen_at = observed_at or timezone.now()
    if not any((external_id, address, city, latitude is not None, longitude is not None)):
        return None

    with transaction.atomic():
        existing = _find_existing_location(
            seller_lead,
            external_id=external_id,
            normalized_address=normalized,
        )
        if existing is None:
            existing = SellerLeadLocation.objects.create(
                seller_lead=seller_lead,
                source=source,
                external_id=external_id,
                name=name,
                city=city,
                address=address,
                normalized_address=normalized,
                latitude=latitude,
                longitude=longitude,
                is_primary=False,
                confidence=confidence,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
            )
        else:
            existing.last_seen_at = seen_at
            update_fields = ['last_seen_at', 'updated_at']
            if name:
                existing.name = name
                update_fields.append('name')
            if city:
                existing.city = city
                update_fields.append('city')
            if address:
                existing.address = address
                existing.normalized_address = normalized
                update_fields.extend(['address', 'normalized_address'])
            if latitude is not None:
                existing.latitude = latitude
                update_fields.append('latitude')
            if longitude is not None:
                existing.longitude = longitude
                update_fields.append('longitude')
            if confidence is not None:
                existing.confidence = confidence
                update_fields.append('confidence')
            if source is not None and existing.source_id is None:
                existing.source = source
                update_fields.append('source')
            if external_id and not existing.external_id:
                existing.external_id = external_id
                update_fields.append('external_id')
            existing.save(update_fields=update_fields)

        if existing.is_primary:
            lead = existing.seller_lead
            if lead.normalized_address != existing.normalized_address:
                lead.normalized_address = existing.normalized_address
                lead.save(update_fields=['normalized_address', 'updated_at'])
            return existing

        has_primary = SellerLeadLocation.objects.filter(
            seller_lead=seller_lead,
            is_primary=True,
        ).exists()
        if make_primary_if_missing and not has_primary:
            return set_primary_location(existing)
        return existing


def _find_existing_location(
    seller_lead,
    *,
    external_id: str,
    normalized_address: str,
) -> SellerLeadLocation | None:
    if external_id:
        found = SellerLeadLocation.objects.select_for_update().filter(
            seller_lead=seller_lead,
            external_id=external_id,
        ).first()
        if found is not None:
            return found
    if normalized_address:
        return SellerLeadLocation.objects.select_for_update().filter(
            seller_lead=seller_lead,
            normalized_address=normalized_address,
        ).first()
    return None
