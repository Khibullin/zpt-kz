from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction

from core.kazakhstan_locations import (
    FIRST_CIRCLE_CITIES,
    FIRST_CIRCLE_SELLER_SATURATION_TARGET,
    KAZAKHSTAN_CITY_ALIASES,
    canonical_kazakhstan_city,
)
from core.models import (
    Seller,
    SellerLead,
    SellerLeadDuplicateMatch,
)
from core.services.seller_contact_enrichment import (
    SellerContactEnrichmentError,
    enrich_seller_lead_contacts,
)
from core.services.seller_discovery_runner import (
    DiscoveryRunStats,
    SellerDiscoveryRunError,
    run_seller_discovery,
)
from core.services.seller_lead_admin_workflow import (
    find_request_seller_by_whatsapp,
    normalize_request_seller_whatsapp,
)
from core.services.seller_lead_classification import classify_seller_lead
from core.services.seller_lead_qualification import (
    QUALIFICATION_QUALIFIED,
    qualification_status,
)


DEFAULT_DAILY_TARGET_PER_CITY = 3
DEFAULT_DISCOVERY_LIMIT = 20
DEFAULT_DISCOVERY_MAX_HITS = 50
DEFAULT_CANDIDATES_PER_CITY = 8

CORE_DIRECTIONS = (
    'автозапчасти',
    'авторазбор',
)
ROTATING_DIRECTIONS = (
    'запчасти для китайских автомобилей',
    'автозапчасти оптом',
    'запчасти Hyundai Kia',
    'запчасти Toyota Lexus',
    'запчасти Mercedes-Benz BMW',
    'грузовые автозапчасти',
    'кузовные запчасти',
    'автозапчасти б/у',
)


@dataclass
class CityGrowthResult:
    city: str
    active_before: int
    active_after: int = 0
    target: int = DEFAULT_DAILY_TARGET_PER_CITY
    saturated: bool = False
    activated_lead_ids: list[int] = field(default_factory=list)
    discovery_created: int = 0
    discovery_updated: int = 0
    discovery_hits: int = 0
    discovery_errors: int = 0
    enriched: int = 0
    skipped_candidates: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def activated(self) -> int:
        return len(self.activated_lead_ids)


@dataclass(frozen=True)
class LeadActivationResult:
    lead_id: int
    seller_id: int
    created_seller: bool
    activated: bool
    reason: str = ''


def configured_discovery_providers() -> list[str]:
    if not bool(getattr(settings, 'SELLER_DISCOVERY_ENABLED', False)):
        return []
    providers: list[str] = []
    if (
        bool(getattr(settings, 'SELLER_DISCOVERY_BRAVE_WEB_ENABLED', False))
        and bool((getattr(settings, 'BRAVE_SEARCH_API_KEY', '') or '').strip())
    ):
        providers.append('brave')
    if (
        bool(getattr(settings, 'SELLER_DISCOVERY_2GIS_ENABLED', False))
        and bool((getattr(settings, 'TWO_GIS_API_KEY', '') or '').strip())
    ):
        providers.append('two_gis')
    return providers


def city_active_request_seller_count(city: str) -> int:
    canonical = canonical_kazakhstan_city(city)
    if canonical is None:
        return 0
    return Seller.objects.filter(
        city=canonical,
        is_active=True,
        is_paused=False,
        receive_requests=True,
    ).exclude(whatsapp='').count()


def is_city_saturated(
    city: str,
    *,
    threshold: int = FIRST_CIRCLE_SELLER_SATURATION_TARGET,
) -> bool:
    return city_active_request_seller_count(city) >= threshold


def daily_directions(*, day_index: int) -> tuple[str, ...]:
    rotating = ROTATING_DIRECTIONS[day_index % len(ROTATING_DIRECTIONS)]
    return (*CORE_DIRECTIONS, rotating)


def _unresolved_duplicate(lead: SellerLead) -> bool:
    if lead.duplicate_of_id:
        return True
    possible = SellerLeadDuplicateMatch.STATUS_POSSIBLE
    return (
        lead.duplicate_matches_as_a.filter(status=possible).exists()
        or lead.duplicate_matches_as_b.filter(status=possible).exists()
    )


def _is_kz_mobile_whatsapp(value: str | None) -> bool:
    digits = normalize_request_seller_whatsapp(value)
    # Kazakhstan mobile WhatsApp numbers use +7 6xx / +7 7xx ranges.
    # +7 9xx is a Russian mobile range and must not be auto-activated as KZ.
    return len(digits) == 11 and digits.startswith(('76', '77'))


def _city_markers(city: str) -> tuple[str, ...]:
    markers = [city]
    markers.extend(
        alias
        for alias, canonical in KAZAKHSTAN_CITY_ALIASES.items()
        if canonical == city
    )
    return tuple(dict.fromkeys(markers))


def _text_mentions_city(text: str, city: str) -> bool:
    folded = ' '.join(str(text or '').casefold().replace('ё', 'е').split())
    if not folded:
        return False
    for marker in _city_markers(city):
        needle = ' '.join(str(marker or '').casefold().replace('ё', 'е').split())
        if not needle:
            continue
        if re.search(r'(?<![\w])' + re.escape(needle) + r'(?![\w])', folded):
            return True
    return False


def lead_city_is_confirmed(lead: SellerLead) -> bool:
    city = canonical_kazakhstan_city(lead.city)
    if city is None:
        return False

    # Business-owned text can corroborate the city. Search query text is never
    # used because it is only the requested discovery location.
    for value in (
        lead.name,
        lead.profile_description,
        lead.category,
    ):
        if _text_mentions_city(value, city):
            return True

    for source in lead.sources.all().order_by('-last_seen_at', '-pk')[:20]:
        if source.provider == 'two_gis':
            metadata_city = canonical_kazakhstan_city(
                (source.metadata or {}).get('city')
            )
            if metadata_city == city:
                return True
        if _text_mentions_city(source.display_name, city):
            return True

    for location in lead.locations.all():
        if canonical_kazakhstan_city(location.city) != city:
            continue
        source = getattr(location, 'source', None)
        if source is not None and source.provider == 'two_gis':
            return True
    return False


def lead_is_safe_for_auto_activation(lead: SellerLead) -> bool:
    if lead.request_seller_id:
        return False
    if lead.lifecycle_status in {
        SellerLead.LIFECYCLE_DUPLICATE,
        SellerLead.LIFECYCLE_REJECTED,
        SellerLead.LIFECYCLE_CLOSED,
    }:
        return False
    if _unresolved_duplicate(lead):
        return False
    if qualification_status(lead) != QUALIFICATION_QUALIFIED:
        return False
    if lead.whatsapp_confidence != 'high':
        return False
    if not _is_kz_mobile_whatsapp(lead.whatsapp):
        return False
    if canonical_kazakhstan_city(lead.city) is None:
        return False
    return lead_city_is_confirmed(lead)


def _discovered_brand_objects(lead: SellerLead):
    return [
        link.brand
        for link in lead.brand_links.select_related('brand').all()
        if link.brand_id
    ]


def _discovered_category_objects(lead: SellerLead):
    return [
        link.category
        for link in lead.category_links.select_related('category').all()
        if link.category_id
    ]


def _source_text(lead: SellerLead) -> str:
    fragments: list[str] = [
        lead.name or '',
        lead.category or '',
        lead.profile_description or '',
    ]
    for source in lead.sources.all()[:10]:
        if source.display_name:
            fragments.append(source.display_name)
        if source.metadata:
            fragments.append(json.dumps(source.metadata, ensure_ascii=False, default=str))
    return ' '.join(fragments).casefold()


def infer_transport_type(lead: SellerLead, brands) -> str:
    selected = (lead.request_seller_transport_type or '').strip()
    if selected in {'car', 'truck'}:
        return selected
    transport_types = {brand.transport_type for brand in brands if brand.transport_type}
    if transport_types == {'truck'}:
        return 'truck'
    if 'груз' in _source_text(lead) or 'truck' in _source_text(lead):
        return 'truck'
    return 'car'


def seller_type_from_lead(lead: SellerLead) -> str:
    if lead.business_type in {'service_parts', 'mixed'}:
        return 'both'
    return 'seller'


def activate_qualified_lead_for_requests(lead: SellerLead) -> LeadActivationResult:
    """Activate one verified SellerLead for buyer requests.

    No marketplace user or SellerProfile is created and no marketing consent is
    granted. Existing registered sellers keep their own specialization and
    pause/receive settings; they are only linked to the lead.
    """
    lead.refresh_from_db()
    if not lead_is_safe_for_auto_activation(lead):
        return LeadActivationResult(
            lead_id=lead.pk,
            seller_id=lead.request_seller_id or 0,
            created_seller=False,
            activated=False,
            reason='not_eligible',
        )

    city = canonical_kazakhstan_city(lead.city)
    whatsapp = normalize_request_seller_whatsapp(lead.whatsapp)
    if city is None or not whatsapp:
        return LeadActivationResult(
            lead_id=lead.pk,
            seller_id=0,
            created_seller=False,
            activated=False,
            reason='invalid_identity',
        )

    brands = _discovered_brand_objects(lead)
    categories = _discovered_category_objects(lead)
    transport_type = infer_transport_type(lead, brands)
    seller_type = seller_type_from_lead(lead)

    with transaction.atomic():
        locked = SellerLead.objects.select_for_update().get(pk=lead.pk)
        if _unresolved_duplicate(locked):
            return LeadActivationResult(
                lead_id=locked.pk,
                seller_id=locked.request_seller_id or 0,
                created_seller=False,
                activated=False,
                reason='duplicate_review',
            )

        existing = find_request_seller_by_whatsapp(whatsapp)
        seller = None
        created = False

        if locked.request_seller_id:
            return LeadActivationResult(
                lead_id=locked.pk,
                seller_id=locked.request_seller_id,
                created_seller=False,
                activated=False,
                reason='already_linked',
            )
        if existing is not None:
            return LeadActivationResult(
                lead_id=locked.pk,
                seller_id=existing.pk,
                created_seller=False,
                activated=False,
                reason='existing_seller',
            )

        seller = Seller.objects.create(
            name=(locked.name or '')[:255],
            whatsapp=whatsapp[:20],
            city=city[:100],
            transport_type=transport_type,
            seller_type=seller_type,
            notes=f'SellerLead #{locked.pk}; automatic request activation',
            receive_requests=True,
            is_active=True,
            is_paused=False,
            all_categories=not bool(categories),
            all_countries=True,
            all_brands=not bool(brands),
            all_models=True,
        )
        locked.request_seller = seller
        created = True

        seller.name = (locked.name or seller.name)[:255]
        seller.whatsapp = whatsapp[:20]
        seller.city = city[:100]
        seller.transport_type = transport_type
        seller.seller_type = seller_type
        seller.is_active = True
        seller.is_paused = False
        seller.receive_requests = True
        seller.all_categories = not bool(categories)
        seller.all_countries = True
        seller.all_brands = not bool(brands)
        seller.all_models = True
        seller.category = ''
        seller.brand = ''
        seller.model = ''
        seller.country_fk = None
        seller.brand_fk = None
        seller.model_fk = None
        seller.save(update_fields=[
            'name', 'whatsapp', 'city', 'transport_type', 'seller_type',
            'is_active', 'is_paused', 'receive_requests',
            'all_categories', 'all_countries', 'all_brands', 'all_models',
            'category', 'brand', 'model', 'country_fk', 'brand_fk', 'model_fk',
        ])

        seller.selected_categories.clear()
        seller.selected_countries.clear()
        seller.selected_brands.clear()
        seller.selected_models.clear()
        if categories:
            seller.selected_categories.add(*categories)
        if brands:
            compatible_brands = [
                brand for brand in brands
                if brand.transport_type == transport_type
            ]
            if compatible_brands:
                seller.selected_brands.add(*compatible_brands)
            else:
                seller.all_brands = True
                seller.save(update_fields=['all_brands'])

        locked.city = city
        locked.request_seller_transport_type = transport_type
        locked.review_status = SellerLead.REVIEW_CONVERTED_REQUESTS
        locked.lifecycle_status = SellerLead.LIFECYCLE_ACTIVE
        locked.save(update_fields=[
            'city',
            'request_seller',
            'request_seller_transport_type',
            'review_status',
            'lifecycle_status',
            'updated_at',
        ])

    return LeadActivationResult(
        lead_id=lead.pk,
        seller_id=seller.pk,
        created_seller=created,
        activated=True,
        reason='activated',
    )


def _city_candidate_ids(city: str, preferred_ids, *, limit: int) -> list[int]:
    canonical = canonical_kazakhstan_city(city)
    if canonical is None:
        return []

    ordered: list[int] = []
    seen: set[int] = set()

    preferred_order = [
        int(lead_id)
        for lead_id in preferred_ids
        if lead_id
    ]
    valid_preferred = set(
        SellerLead.objects.filter(
            pk__in=preferred_order,
            city=canonical,
            request_seller__isnull=True,
        )
        .exclude(lifecycle_status__in=[
            SellerLead.LIFECYCLE_DUPLICATE,
            SellerLead.LIFECYCLE_REJECTED,
            SellerLead.LIFECYCLE_CLOSED,
        ])
        .values_list('pk', flat=True)
    )
    for lead_id in preferred_order:
        if lead_id in seen or lead_id not in valid_preferred:
            continue
        seen.add(lead_id)
        ordered.append(lead_id)

    queryset = (
        SellerLead.objects.filter(
            city=canonical,
            request_seller__isnull=True,
        )
        .exclude(lifecycle_status__in=[
            SellerLead.LIFECYCLE_DUPLICATE,
            SellerLead.LIFECYCLE_REJECTED,
            SellerLead.LIFECYCLE_CLOSED,
        ])
        .order_by('-last_seen_at', '-pk')
        .values_list('pk', flat=True)
    )
    for lead_id in queryset:
        if lead_id in seen:
            continue
        seen.add(lead_id)
        ordered.append(lead_id)
        if len(ordered) >= limit:
            break
    return ordered[:limit]


def _prepare_and_activate_candidate(lead: SellerLead) -> tuple[bool, bool, str]:
    """Return (activated, enriched, reason)."""
    try:
        classify_seller_lead(lead, promote_lifecycle=True)
    except Exception as exc:
        return False, False, f'classify:{type(exc).__name__}'

    lead.refresh_from_db()
    if lead_is_safe_for_auto_activation(lead):
        result = activate_qualified_lead_for_requests(lead)
        return result.activated, False, result.reason

    if qualification_status(lead) != QUALIFICATION_QUALIFIED:
        return False, False, 'not_qualified'
    if _unresolved_duplicate(lead):
        return False, False, 'duplicate_review'

    enriched = False
    if not lead.whatsapp or lead.whatsapp_confidence != 'high':
        try:
            enrich_seller_lead_contacts(
                lead,
                sources=('all',),
                dry_run=False,
                stop_on_verified_whatsapp=True,
            )
            enriched = True
        except SellerContactEnrichmentError:
            return False, False, 'enrichment_controlled_error'
        except Exception as exc:
            return False, False, f'enrichment:{type(exc).__name__}'

    try:
        lead.refresh_from_db()
        classify_seller_lead(lead, promote_lifecycle=True)
        lead.refresh_from_db()
    except Exception as exc:
        return False, enriched, f'reclassify:{type(exc).__name__}'

    if not lead_is_safe_for_auto_activation(lead):
        return False, enriched, 'no_verified_whatsapp'

    result = activate_qualified_lead_for_requests(lead)
    return result.activated, enriched, result.reason


def _process_candidate_ids(
    candidate_ids,
    result: CityGrowthResult,
    *,
    target: int,
    processed: set[int],
) -> None:
    for lead_id in candidate_ids:
        if result.activated >= target:
            break
        lead_id = int(lead_id)
        if lead_id in processed:
            continue
        processed.add(lead_id)
        lead = SellerLead.objects.filter(pk=lead_id).first()
        if lead is None:
            continue
        if lead.request_seller_id:
            result.skipped_candidates += 1
            continue
        if canonical_kazakhstan_city(lead.city) != result.city:
            result.skipped_candidates += 1
            continue
        activated, enriched, reason = _prepare_and_activate_candidate(lead)
        if enriched:
            result.enriched += 1
        if activated:
            result.activated_lead_ids.append(lead_id)
        else:
            result.skipped_candidates += 1
            if reason.startswith(('classify:', 'enrichment:', 'reclassify:')):
                result.errors.append(f'lead#{lead_id}:{reason}')


def grow_first_circle_city(
    city: str,
    *,
    day_index: int,
    target: int = DEFAULT_DAILY_TARGET_PER_CITY,
    saturation_threshold: int = FIRST_CIRCLE_SELLER_SATURATION_TARGET,
    candidate_limit: int = DEFAULT_CANDIDATES_PER_CITY,
    dry_run: bool = False,
) -> CityGrowthResult:
    canonical = canonical_kazakhstan_city(city)
    if canonical not in FIRST_CIRCLE_CITIES:
        raise ValueError(f'{city} не входит в первый круг Seller Growth.')

    active_before = city_active_request_seller_count(canonical)
    result = CityGrowthResult(
        city=canonical,
        active_before=active_before,
        active_after=active_before,
        target=target,
        saturated=active_before >= saturation_threshold,
    )
    if result.saturated or dry_run:
        return result

    # Use already collected leads first. New provider calls are made only when
    # the city's daily target cannot be reached from the existing backlog.
    processed: set[int] = set()
    backlog_ids = _city_candidate_ids(
        canonical,
        (),
        limit=candidate_limit,
    )
    _process_candidate_ids(
        backlog_ids,
        result,
        target=target,
        processed=processed,
    )
    if result.activated >= target:
        result.active_after = city_active_request_seller_count(canonical)
        return result

    providers = configured_discovery_providers()
    preferred_ids: list[int] = []

    if providers:
        try:
            discovery: DiscoveryRunStats = run_seller_discovery(
                provider_names=providers,
                cities=[canonical],
                directions=list(daily_directions(day_index=day_index)),
                limit=DEFAULT_DISCOVERY_LIMIT,
                max_hits=DEFAULT_DISCOVERY_MAX_HITS,
                max_queries=min(6, len(providers) * 3),
                max_pages=2,
                dry_run=False,
            )
            result.discovery_created = discovery.created
            result.discovery_updated = discovery.updated
            result.discovery_hits = discovery.hits_received
            result.discovery_errors = discovery.errors
            preferred_ids = [
                outcome.seller_lead_id
                for outcome in discovery.outcomes
                if outcome.seller_lead_id
            ]
        except SellerDiscoveryRunError as exc:
            result.errors.append(f'discovery:{exc}')
        except Exception as exc:
            result.errors.append(f'discovery:{type(exc).__name__}:{exc}')
    else:
        result.errors.append('discovery:no_configured_provider')

    remaining_ids = _city_candidate_ids(
        canonical,
        preferred_ids,
        limit=candidate_limit,
    )
    _process_candidate_ids(
        remaining_ids,
        result,
        target=target,
        processed=processed,
    )

    result.active_after = city_active_request_seller_count(canonical)
    return result
