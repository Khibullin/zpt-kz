"""Persist discovery hits into the SellerLead foundation.

Package 2 only collects SellerLead data. A later package may treat a found
lead as a potential recipient of buyer requests and count how many requests
would have matched that shop. That future mode still must not create
core.Seller, must not call convert_lead_to_request_seller(), and must not
set receive_requests.

FUTURE_MATCHING_ROLE records that intent. This module does not match requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    SellerLead,
    SellerLeadEvidence,
    SellerLeadSource,
)
from core.services.seller_discovery_dedup import find_possible_duplicates_for_leads
from core.services.seller_discovery_identity import (
    normalize_address,
    normalize_domain,
    normalize_instagram_identity,
    normalize_seller_name,
    normalize_seller_phone,
    refresh_seller_lead_identity,
)
from core.services.seller_discovery_providers.base import SellerDiscoveryHit, payload_hash
from core.services.seller_discovery_sources import (
    add_seller_lead_evidence,
    upsert_seller_lead_location,
    upsert_seller_lead_source,
)

FUTURE_MATCHING_ROLE = 'potential_recipient'

GENERIC_DOMAINS = frozenset({
    'instagram.com',
    'facebook.com',
    'vk.com',
    'ok.ru',
    'youtube.com',
    '2gis.kz',
    '2gis.com',
    '2gis.ru',
    'wa.me',
    'api.whatsapp.com',
    'taplink.cc',
    'linktr.ee',
    't.me',
    'telegram.me',
    'google.com',
    'google.kz',
    'yandex.ru',
    'yandex.kz',
})

LEAD_SOURCE_TYPE = {
    SellerLeadSource.SOURCE_TWO_GIS: 'other',
    SellerLeadSource.SOURCE_INSTAGRAM: 'instagram_profile',
    SellerLeadSource.SOURCE_BRAVE_SEARCH: 'web_search',
    SellerLeadSource.SOURCE_WEB_SEARCH: 'web_search',
}


class SellerDiscoveryIngestionError(ValueError):
    pass


@dataclass(frozen=True)
class IngestionResult:
    action: str
    match_reason: str
    name: str
    city: str
    provider: str
    external_id: str
    seller_lead_id: int | None = None
    possible_duplicate_ids: tuple[int, ...] = ()
    dry_run: bool = False


def ingest_seller_discovery_hit(
    hit: SellerDiscoveryHit,
    *,
    dry_run: bool = False,
) -> IngestionResult:
    """Create or enrich one SellerLead. dry_run reads matches and writes nothing."""
    _validate_hit(hit)
    if not str(hit.name or '').strip():
        return IngestionResult(
            action='skipped',
            match_reason='empty_name',
            name='',
            city=hit.city,
            provider=hit.provider,
            external_id=hit.external_id,
            dry_run=dry_run,
        )

    if dry_run:
        lead, reason = _find_existing_lead(hit)
        return IngestionResult(
            action='update' if lead is not None else 'create',
            match_reason=reason or 'created',
            name=hit.name,
            city=hit.city,
            provider=hit.provider,
            external_id=hit.external_id,
            seller_lead_id=lead.pk if lead is not None else None,
            dry_run=True,
        )

    with transaction.atomic():
        lead, reason = _find_existing_lead(hit, lock=True)
        created = lead is None
        if created:
            lead = _create_lead(hit)
            if not reason:
                reason = 'created'
        else:
            _enrich_lead(lead, hit)
        source = _write_source(lead, hit)
        _write_evidence(lead, hit, source)
        _write_location(lead, hit, source)
        refresh_seller_lead_identity(lead)
        lead.last_seen_at = hit.observed_at or timezone.now()
        lead.save(update_fields=['last_seen_at', 'updated_at'])
        lead.refresh_from_db()
        matches = find_possible_duplicates_for_leads([lead])
        duplicate_ids = tuple(match.pk for match in matches)
        lead.refresh_from_db()

    return IngestionResult(
        action='create' if created else 'update',
        match_reason=reason,
        name=lead.name,
        city=lead.city,
        provider=hit.provider,
        external_id=hit.external_id,
        seller_lead_id=lead.pk,
        possible_duplicate_ids=duplicate_ids,
        dry_run=False,
    )


def _validate_hit(hit: SellerDiscoveryHit) -> None:
    if not str(hit.provider or '').strip():
        raise SellerDiscoveryIngestionError('У наблюдения не указан провайдер.')
    if not str(hit.source_type or '').strip():
        raise SellerDiscoveryIngestionError('У наблюдения не указан тип источника.')
    allowed = {choice for choice, _label in SellerLeadSource._meta.get_field('source_type').choices}
    if hit.source_type not in allowed:
        raise SellerDiscoveryIngestionError(f'Неизвестный тип источника: {hit.source_type}')
    if hit.confidence < 0 or hit.confidence > 100:
        raise SellerDiscoveryIngestionError('Уверенность источника должна быть от 0 до 100.')


def _find_existing_lead(hit: SellerDiscoveryHit, *, lock: bool = False):
    """Reuse one lead only when a key matches exactly one row.

    External id is provider + external_id. Source URL, phone, Instagram and
    domain reuse only a single candidate. Two or more candidates are ambiguous:
    the caller creates a new lead and leaves duplicate review to dedup.
    A name alone is never an identity. Name + address + city is an exact match.
    """
    queryset = SellerLead.objects.all()
    if lock:
        queryset = queryset.select_for_update()

    if hit.external_id:
        lead, ambiguous = _unique_lead(
            SellerLeadSource.objects.filter(
                provider=hit.provider,
                external_id=hit.external_id,
            ).values_list('seller_lead_id', flat=True),
            queryset,
        )
        if ambiguous:
            return None, 'ambiguous_external_id'
        if lead is not None:
            return lead, 'external_id'

    if hit.source_url:
        lead, ambiguous = _unique_lead(
            SellerLeadSource.objects.filter(source_url=hit.source_url).values_list(
                'seller_lead_id',
                flat=True,
            ),
            queryset,
        )
        if ambiguous:
            return None, 'ambiguous_source_url'
        if lead is not None:
            return lead, 'source_url'

    phone_ids: list[int] = []
    for phone in _match_phones(hit):
        phone_ids.extend(_phone_lead_ids(phone))
    lead, ambiguous = _unique_from_ids(phone_ids, queryset)
    if ambiguous:
        return None, 'ambiguous_phone'
    if lead is not None:
        return lead, 'phone'

    instagram = normalize_instagram_identity(hit.instagram_url)
    if instagram:
        lead, ambiguous = _unique_lead(
            SellerLead.objects.filter(
                Q(instagram_username__iexact=instagram) | Q(normalized_instagram=instagram)
                | Q(evidences__field_name='instagram', evidences__normalized_value=instagram),
            ).values_list('pk', flat=True),
            queryset,
        )
        if ambiguous:
            return None, 'ambiguous_instagram'
        if lead is not None:
            return lead, 'instagram'

    domain = _identity_domain(hit.website)
    if domain:
        lead, ambiguous = _unique_lead(
            SellerLead.objects.filter(
                Q(normalized_domain=domain)
                | Q(
                    evidences__field_name__in=('website', 'domain'),
                    evidences__normalized_value=domain,
                ),
            ).values_list('pk', flat=True),
            queryset,
        )
        if ambiguous:
            return None, 'ambiguous_domain'
        if lead is not None:
            return lead, 'domain'

    name = normalize_seller_name(hit.name)
    address = normalize_address(hit.address)
    city = str(hit.city or '').strip()
    if name and address and city:
        lead, ambiguous = _unique_lead(
            SellerLead.objects.filter(
                Q(normalized_name=name, normalized_address=address, city__iexact=city)
                | Q(
                    normalized_name=name,
                    city__iexact=city,
                    locations__normalized_address=address,
                ),
            ).values_list('pk', flat=True),
            queryset,
        )
        if ambiguous:
            return None, 'ambiguous_name_address_city'
        if lead is not None:
            return lead, 'name_address_city'
    return None, ''


def _unique_lead(id_queryset, lock_queryset):
    seen: list[int] = []
    for pk in id_queryset.iterator(chunk_size=50):
        if pk in seen:
            continue
        seen.append(pk)
        if len(seen) > 1:
            return None, True
    return _unique_from_ids(seen, lock_queryset)


def _unique_from_ids(ids, lock_queryset):
    unique_ids = list(dict.fromkeys(ids))
    if len(unique_ids) > 1:
        return None, True
    if len(unique_ids) == 1:
        return lock_queryset.filter(pk=unique_ids[0]).first(), False
    return None, False


def _phone_lead_ids(phone: str) -> list[int]:
    return list(
        SellerLead.objects.filter(
            Q(whatsapp=phone) | Q(normalized_phone=phone)
            | Q(
                evidences__field_name__in=('phone', 'whatsapp'),
                evidences__normalized_value=phone,
            ),
        ).values_list('pk', flat=True).distinct()
    )


def _match_phones(hit: SellerDiscoveryHit) -> list[str]:
    raw_values = list(hit.phones)
    if hit.phone:
        raw_values.append(hit.phone)
    if hit.whatsapp_phone:
        raw_values.append(hit.whatsapp_phone)
    phones: list[str] = []
    for raw in raw_values:
        normalized = normalize_seller_phone(raw)
        if normalized and len(normalized) == 11 and normalized.startswith('7') and normalized not in phones:
            phones.append(normalized)
    return phones


def _explicit_whatsapp(hit: SellerDiscoveryHit) -> str:
    normalized = normalize_seller_phone(hit.whatsapp_phone)
    if normalized and len(normalized) == 11 and normalized.startswith('7'):
        return normalized
    return ''


def _create_lead(hit: SellerDiscoveryHit) -> SellerLead:
    observed = hit.observed_at or timezone.now()
    whatsapp = _explicit_whatsapp(hit)
    instagram = normalize_instagram_identity(hit.instagram_url)
    website = _identity_website(hit.website)
    if whatsapp and _value_taken(SellerLead, 'whatsapp', whatsapp):
        whatsapp = ''
    if instagram and _username_taken(instagram):
        instagram = ''
    lead = SellerLead(
        name=hit.name[:255],
        city=str(hit.city or '')[:100],
        category=str(hit.category_text or '')[:100],
        car_brands='',
        website_url=website,
        instagram_username=instagram,
        instagram_url=(hit.instagram_url if instagram else '')[:500],
        whatsapp=whatsapp,
        source_url=str(hit.source_url or '')[:500],
        source_type=LEAD_SOURCE_TYPE.get(hit.source_type, 'other'),
        status=SellerLead.STATUS_NEEDS_REVIEW,
        lifecycle_status=SellerLead.LIFECYCLE_FOUND,
        marketplace_invitation_status='',
        overall_confidence=hit.confidence,
        last_seen_at=observed,
        collected_at=observed,
    )
    try:
        lead.save()
    except IntegrityError as exc:
        raise SellerDiscoveryIngestionError(
            'Не удалось создать найденного продавца: идентификатор уже занят.',
        ) from exc
    refresh_seller_lead_identity(lead)
    return lead


def _enrich_lead(lead: SellerLead, hit: SellerDiscoveryHit) -> None:
    changed: list[str] = []
    if _fill(lead, 'name', hit.name, 255):
        changed.append('name')
    if _fill(lead, 'city', hit.city, 100):
        changed.append('city')
    if _fill(lead, 'category', hit.category_text, 100):
        changed.append('category')
    website = _identity_website(hit.website)
    if website and _fill(lead, 'website_url', website, 500):
        changed.append('website_url')
    instagram = normalize_instagram_identity(hit.instagram_url)
    if instagram and not lead.instagram_username and not _username_taken(instagram, exclude_pk=lead.pk):
        lead.instagram_username = instagram
        lead.instagram_url = str(hit.instagram_url or '')[:500]
        changed.extend(['instagram_username', 'instagram_url'])
    whatsapp = _explicit_whatsapp(hit)
    if whatsapp and not lead.whatsapp and not _value_taken(
        SellerLead,
        'whatsapp',
        whatsapp,
        exclude_pk=lead.pk,
    ):
        lead.whatsapp = whatsapp
        changed.append('whatsapp')
    if not lead.source_url and hit.source_url:
        lead.source_url = hit.source_url[:500]
        changed.append('source_url')
    if lead.overall_confidence is None:
        lead.overall_confidence = hit.confidence
        changed.append('overall_confidence')
    if changed:
        lead.last_enriched_at = hit.observed_at or timezone.now()
        changed.append('last_enriched_at')
        lead.save(update_fields=[*changed, 'updated_at'])


def _write_source(lead: SellerLead, hit: SellerDiscoveryHit) -> SellerLeadSource:
    metadata = {
        'city': hit.city,
        'rubrics': list(hit.rubrics),
    }
    if hit.org_id:
        metadata['org_id'] = hit.org_id
    if hit.search_query:
        metadata['search_query'] = hit.search_query
    if hit.brand_name:
        metadata['brand_name'] = hit.brand_name
    return upsert_seller_lead_source(
        lead,
        source_type=hit.source_type,
        provider=hit.provider,
        external_id=hit.external_id,
        source_url=hit.source_url,
        display_name=hit.name,
        fetched_at=hit.observed_at,
        source_confidence=hit.confidence,
        metadata=metadata,
        raw_payload_hash=payload_hash(hit.raw_data or {'name': hit.name, 'external_id': hit.external_id}),
        observed_at=hit.observed_at,
    )


def _write_evidence(lead: SellerLead, hit: SellerDiscoveryHit, source: SellerLeadSource) -> None:
    method = (
        SellerLeadEvidence.METHOD_PARSER
        if hit.provider == 'two_gis'
        else SellerLeadEvidence.METHOD_SEARCH_RESULT
    )
    observed = hit.observed_at or timezone.now()
    fields: list[tuple[str, str]] = [('name', hit.name)]
    if hit.city:
        fields.append(('city', hit.city))
    if hit.address:
        fields.append(('address', hit.address))
    if hit.website:
        fields.append(('website', hit.website))
    if hit.instagram_url:
        fields.append(('instagram', hit.instagram_url))
    if hit.category_text:
        fields.append(('category', hit.category_text))
    if hit.rubrics:
        fields.append(('rubrics', ', '.join(hit.rubrics)))
    if hit.brand_name:
        fields.append(('brand', hit.brand_name))
    for phone in hit.phones or ((hit.phone,) if hit.phone else ()):
        if phone:
            fields.append(('phone', phone))
    if hit.whatsapp_phone:
        fields.append(('whatsapp', hit.whatsapp_phone))
    for field_name, value in fields:
        selected_exists = SellerLeadEvidence.objects.filter(
            seller_lead=lead,
            field_name=field_name,
            is_selected=True,
        ).exists()
        add_seller_lead_evidence(
            lead,
            field_name=field_name,
            value=value,
            source=source,
            confidence=hit.confidence,
            extraction_method=method,
            observed_at=observed,
            is_selected=not selected_exists,
        )


def _write_location(lead: SellerLead, hit: SellerDiscoveryHit, source: SellerLeadSource) -> None:
    if not hit.address and hit.latitude is None and hit.longitude is None:
        return
    upsert_seller_lead_location(
        lead,
        source=source,
        external_id=hit.external_id,
        name=hit.name,
        city=hit.city,
        address=hit.address,
        latitude=hit.latitude if isinstance(hit.latitude, Decimal) else hit.latitude,
        longitude=hit.longitude if isinstance(hit.longitude, Decimal) else hit.longitude,
        confidence=hit.confidence,
        observed_at=hit.observed_at,
    )


def _identity_domain(website: str) -> str:
    domain = normalize_domain(website)
    if not domain or domain in GENERIC_DOMAINS:
        return ''
    return domain


def _identity_website(website: str) -> str:
    raw = str(website or '').strip()
    if not raw:
        return ''
    if not raw.startswith(('http://', 'https://')):
        raw = 'https://' + raw
    parts = urlsplit(raw)
    if not parts.netloc or any(char.isspace() for char in raw):
        return ''
    if not _identity_domain(raw):
        return ''
    return raw[:500]


def _username_taken(username: str, *, exclude_pk: int | None = None) -> bool:
    queryset = SellerLead.objects.filter(instagram_username__iexact=username)
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


def _value_taken(model, field_name: str, value: str, *, exclude_pk: int | None = None) -> bool:
    queryset = model.objects.filter(**{field_name: value})
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


def _fill(lead: SellerLead, field_name: str, value: str, limit: int) -> bool:
    current = str(getattr(lead, field_name) or '').strip()
    incoming = str(value or '').strip()
    if current or not incoming:
        return False
    setattr(lead, field_name, incoming[:limit])
    return True
