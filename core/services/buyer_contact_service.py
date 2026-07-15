from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from django.db import transaction

from core.buyer_portal import ensure_buyer_portal_access
from core.models import (
    BUYER_CONTACT_SOURCE_REQUEST,
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CITY_INTEREST_REQUEST_CITY,
    BUYER_CITY_INTEREST_SELECTED_CITY,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_INFORMATION,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_PURPOSE_SERVICE,
    CONTACT_CONSENT_SOURCE_IMPORT,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerCategoryInterest,
    BuyerCityInterest,
    BuyerContact,
    BuyerPortalAccess,
    BuyerVehicle,
    ContactConsent,
    Request,
)
from core.phone_utils import normalize_kz_phone
from core.services.buyer_contact_utils import mask_phone, normalize_buyer_text

logger = logging.getLogger(__name__)

SYNC_STATUS_SYNCED = 'synced'
SYNC_STATUS_SKIPPED_INVALID_PHONE = 'skipped_invalid_phone'
SYNC_STATUS_SKIPPED_UNSAVED_REQUEST = 'skipped_unsaved_request'
SYNC_STATUS_REQUEST_LINK_CONFLICT = 'request_link_conflict'


@dataclass(frozen=True)
class BuyerSyncResult:
    status: str
    buyer_id: int | None
    buyer_created: bool = False
    request_linked: bool = False
    portal_conflict: bool = False
    reason: str = ''


@dataclass(frozen=True)
class RebuildStats:
    vehicles_created: int = 0
    vehicles_updated: int = 0
    vehicles_deleted: int = 0
    categories_created: int = 0
    categories_updated: int = 0
    categories_deleted: int = 0
    cities_created: int = 0
    cities_updated: int = 0
    cities_deleted: int = 0


def sync_buyer_contact_from_request(
    request_obj: Request,
    *,
    rebuild: bool = True,
) -> BuyerSyncResult:
    if not request_obj.pk:
        return BuyerSyncResult(
            status=SYNC_STATUS_SKIPPED_UNSAVED_REQUEST,
            buyer_id=None,
            reason='Request has no primary key.',
        )

    normalized_phone = normalize_kz_phone(request_obj.phone)
    if not normalized_phone:
        return BuyerSyncResult(
            status=SYNC_STATUS_SKIPPED_INVALID_PHONE,
            buyer_id=None,
            reason='Invalid phone number.',
        )

    buyer, buyer_created = _get_or_create_buyer_contact(normalized_phone)
    if buyer_created:
        _ensure_default_consents(buyer)

    portal_conflict = _resolve_portal_access(buyer, normalized_phone)

    if (
        request_obj.buyer_contact_id
        and request_obj.buyer_contact_id != buyer.pk
    ):
        logger.warning(
            'Request #%s already linked to buyer #%s; expected buyer #%s (%s).',
            request_obj.pk,
            request_obj.buyer_contact_id,
            buyer.pk,
            mask_phone(normalized_phone),
        )
        return BuyerSyncResult(
            status=SYNC_STATUS_REQUEST_LINK_CONFLICT,
            buyer_id=request_obj.buyer_contact_id,
            buyer_created=buyer_created,
            portal_conflict=portal_conflict,
            reason='Request is linked to a different buyer.',
        )

    request_linked = False
    if request_obj.buyer_contact_id != buyer.pk:
        request_obj.buyer_contact = buyer
        request_obj.save(update_fields=['buyer_contact'])
        request_linked = True

    if rebuild:
        rebuild_buyer_contact(buyer)

    return BuyerSyncResult(
        status=SYNC_STATUS_SYNCED,
        buyer_id=buyer.pk,
        buyer_created=buyer_created,
        request_linked=request_linked,
        portal_conflict=portal_conflict,
    )


def rebuild_buyer_contact(buyer: BuyerContact) -> BuyerContact:
    stats = _rebuild_buyer_contact(buyer)
    return buyer


def _rebuild_buyer_contact(buyer: BuyerContact) -> RebuildStats:
    with transaction.atomic():
        requests = list(buyer.requests.order_by('created_at', 'id'))
        _rebuild_buyer_aggregates(buyer, requests)
        vehicle_stats = _rebuild_vehicles(buyer, requests)
        category_stats = _rebuild_category_interests(buyer, requests)
        city_stats = _rebuild_city_interests(buyer, requests)
        return RebuildStats(
            vehicles_created=vehicle_stats[0],
            vehicles_updated=vehicle_stats[1],
            vehicles_deleted=vehicle_stats[2],
            categories_created=category_stats[0],
            categories_updated=category_stats[1],
            categories_deleted=category_stats[2],
            cities_created=city_stats[0],
            cities_updated=city_stats[1],
            cities_deleted=city_stats[2],
        )


def _get_or_create_buyer_contact(normalized_phone: str) -> tuple[BuyerContact, bool]:
    return BuyerContact.objects.get_or_create(
        phone_normalized=normalized_phone,
        defaults={
            'source': BUYER_CONTACT_SOURCE_REQUEST,
            'status': BUYER_CONTACT_STATUS_ACTIVE,
        },
    )


def _alt_phone_eight_variant(normalized_phone: str) -> str | None:
    if normalized_phone.startswith('7') and len(normalized_phone) == 11:
        return '8' + normalized_phone[1:]
    return None


def _resolve_portal_access(buyer: BuyerContact, normalized_phone: str) -> bool:
    alt_phone = _alt_phone_eight_variant(normalized_phone)
    canonical_portal = BuyerPortalAccess.objects.filter(
        phone_normalized=normalized_phone,
    ).first()
    alt_portal = None
    if alt_phone:
        alt_portal = BuyerPortalAccess.objects.filter(
            phone_normalized=alt_phone,
        ).first()

    portal_conflict = False
    chosen_portal = None

    if canonical_portal and alt_portal and canonical_portal.pk != alt_portal.pk:
        portal_conflict = True
        chosen_portal = canonical_portal
        logger.warning(
            'BuyerPortalAccess conflict for buyer phone %s: canonical #%s and alt #%s both exist.',
            mask_phone(normalized_phone),
            canonical_portal.pk,
            alt_portal.pk,
        )
    elif canonical_portal:
        chosen_portal = canonical_portal
    elif alt_portal:
        chosen_portal = alt_portal
    else:
        chosen_portal = ensure_buyer_portal_access(normalized_phone)

    if buyer.portal_access_id:
        if chosen_portal and buyer.portal_access_id != chosen_portal.pk:
            portal_conflict = True
            logger.warning(
                'BuyerContact #%s keeps portal access #%s; resolved portal is #%s (%s).',
                buyer.pk,
                buyer.portal_access_id,
                chosen_portal.pk if chosen_portal else None,
                mask_phone(normalized_phone),
            )
        return portal_conflict

    if chosen_portal and buyer.portal_access_id != chosen_portal.pk:
        buyer.portal_access = chosen_portal
        buyer.save(update_fields=['portal_access', 'updated_at'])

    return portal_conflict


def _ensure_default_consents(buyer: BuyerContact) -> None:
    for purpose in (
        CONTACT_CONSENT_PURPOSE_SERVICE,
        CONTACT_CONSENT_PURPOSE_INFORMATION,
        CONTACT_CONSENT_PURPOSE_MARKETING,
    ):
        ContactConsent.objects.get_or_create(
            buyer=buyer,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=purpose,
            defaults={
                'status': CONTACT_CONSENT_STATUS_UNKNOWN,
                'source': CONTACT_CONSENT_SOURCE_IMPORT,
            },
        )


def parse_selected_cities(selected_cities: str) -> list[str]:
    seen_normalized: set[str] = set()
    cities: list[str] = []
    for part in (selected_cities or '').split(','):
        city = part.strip()
        if not city:
            continue
        normalized = normalize_buyer_text(city)
        if normalized in seen_normalized:
            continue
        seen_normalized.add(normalized)
        cities.append(city)
    return cities


def _rebuild_buyer_aggregates(
    buyer: BuyerContact,
    requests: list[Request],
) -> None:
    if not requests:
        buyer.requests_count = 0
        buyer.first_request_at = None
        buyer.last_request_at = None
        buyer.last_search_scope = ''
        buyer.city_scope_requests_count = 0
        buyer.kazakhstan_scope_requests_count = 0
        buyer.custom_scope_requests_count = 0
        buyer.primary_country = ''
        buyer.primary_city = ''
        buyer.save(
            update_fields=[
                'requests_count',
                'first_request_at',
                'last_request_at',
                'last_search_scope',
                'city_scope_requests_count',
                'kazakhstan_scope_requests_count',
                'custom_scope_requests_count',
                'primary_country',
                'primary_city',
                'updated_at',
            ],
        )
        return

    buyer.requests_count = len(requests)
    buyer.first_request_at = requests[0].created_at
    buyer.last_request_at = requests[-1].created_at
    latest_request = max(requests, key=lambda req: (req.created_at, req.id))
    buyer.last_search_scope = latest_request.search_scope or ''
    buyer.city_scope_requests_count = sum(
        1 for req in requests if req.search_scope == 'city'
    )
    buyer.kazakhstan_scope_requests_count = sum(
        1 for req in requests if req.search_scope == 'kazakhstan'
    )
    buyer.custom_scope_requests_count = sum(
        1 for req in requests if req.search_scope == 'custom'
    )
    buyer.primary_country = _most_common_display_value(requests, 'country')
    buyer.primary_city = _most_common_display_value(requests, 'city')
    buyer.save(
        update_fields=[
            'requests_count',
            'first_request_at',
            'last_request_at',
            'last_search_scope',
            'city_scope_requests_count',
            'kazakhstan_scope_requests_count',
            'custom_scope_requests_count',
            'primary_country',
            'primary_city',
            'updated_at',
        ],
    )


def _most_common_display_value(requests: Iterable[Request], field_name: str) -> str:
    counts: Counter[str] = Counter()
    for req in requests:
        raw = str(getattr(req, field_name, '') or '').strip()
        if not raw:
            continue
        counts[normalize_buyer_text(raw)] += 1

    if not counts:
        return ''

    max_count = max(counts.values())
    tied = {norm for norm, count in counts.items() if count == max_count}

    for req in reversed(list(requests)):
        raw = str(getattr(req, field_name, '') or '').strip()
        if not raw:
            continue
        if normalize_buyer_text(raw) in tied:
            return raw

    return next(iter(tied))


def _rebuild_vehicles(buyer: BuyerContact, requests: list[Request]) -> tuple[int, int, int]:
    groups: dict[tuple[str, str, str], list[Request]] = defaultdict(list)
    for req in requests:
        brand = str(req.brand or '').strip()
        model = str(req.model or '').strip()
        if not brand and not model:
            continue
        key = (
            req.transport_type,
            normalize_buyer_text(brand),
            normalize_buyer_text(model),
        )
        groups[key].append(req)

    created = updated = deleted = 0
    seen_keys: set[tuple[str, str, str]] = set()

    for key, group_requests in groups.items():
        group_requests.sort(key=lambda req: (req.created_at, req.id))
        first_req = group_requests[0]
        last_req = group_requests[-1]
        transport_type, brand_norm, model_norm = key
        _, was_created = BuyerVehicle.objects.update_or_create(
            buyer=buyer,
            transport_type=transport_type,
            brand_normalized=brand_norm,
            model_normalized=model_norm,
            defaults={
                'brand': str(last_req.brand or '').strip(),
                'model': str(last_req.model or '').strip(),
                'first_seen_at': first_req.created_at,
                'last_seen_at': last_req.created_at,
                'requests_count': len(group_requests),
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1
        seen_keys.add(key)

    for vehicle in buyer.vehicles.all():
        key = (
            vehicle.transport_type,
            vehicle.brand_normalized,
            vehicle.model_normalized,
        )
        if key not in seen_keys:
            vehicle.delete()
            deleted += 1

    return created, updated, deleted


def _rebuild_category_interests(
    buyer: BuyerContact,
    requests: list[Request],
) -> tuple[int, int, int]:
    groups: dict[str, list[Request]] = defaultdict(list)
    for req in requests:
        category = str(req.category or '').strip()
        if not category:
            continue
        groups[normalize_buyer_text(category)].append(req)

    created = updated = deleted = 0
    seen_keys: set[str] = set()

    for category_norm, group_requests in groups.items():
        group_requests.sort(key=lambda req: (req.created_at, req.id))
        first_req = group_requests[0]
        last_req = group_requests[-1]
        _, was_created = BuyerCategoryInterest.objects.update_or_create(
            buyer=buyer,
            category_normalized=category_norm,
            defaults={
                'category': str(last_req.category or '').strip(),
                'first_seen_at': first_req.created_at,
                'last_seen_at': last_req.created_at,
                'requests_count': len(group_requests),
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1
        seen_keys.add(category_norm)

    for interest in buyer.category_interests.all():
        if interest.category_normalized not in seen_keys:
            interest.delete()
            deleted += 1

    return created, updated, deleted


def _rebuild_city_interests(
    buyer: BuyerContact,
    requests: list[Request],
) -> tuple[int, int, int]:
    groups: dict[tuple[str, str], list[tuple[Request, str]]] = defaultdict(list)

    for req in requests:
        city = str(req.city or '').strip()
        if city:
            key = (normalize_buyer_text(city), BUYER_CITY_INTEREST_REQUEST_CITY)
            groups[key].append((req, city))

        if req.search_scope == 'custom':
            for selected_city in parse_selected_cities(req.selected_cities):
                key = (
                    normalize_buyer_text(selected_city),
                    BUYER_CITY_INTEREST_SELECTED_CITY,
                )
                groups[key].append((req, selected_city))

    created = updated = deleted = 0
    seen_keys: set[tuple[str, str]] = set()

    for (city_norm, interest_type), entries in groups.items():
        entries.sort(key=lambda item: (item[0].created_at, item[0].id))
        first_req, _ = entries[0]
        last_req, display_city = entries[-1]
        _, was_created = BuyerCityInterest.objects.update_or_create(
            buyer=buyer,
            city_normalized=city_norm,
            interest_type=interest_type,
            defaults={
                'city': display_city,
                'first_seen_at': first_req.created_at,
                'last_seen_at': last_req.created_at,
                'requests_count': len(entries),
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1
        seen_keys.add((city_norm, interest_type))

    for interest in buyer.city_interests.all():
        key = (interest.city_normalized, interest.interest_type)
        if key not in seen_keys:
            interest.delete()
            deleted += 1

    return created, updated, deleted
