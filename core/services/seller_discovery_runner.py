"""Manual Seller Discovery run.

This runner is not used by cron and is not part of the Instagram SellerLead
pipeline. It does not send WhatsApp, SMS, or email, and it does not create
Seller, User, SellerProfile, or Product rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.services.seller_discovery_ingestion import (
    IngestionResult,
    SellerDiscoveryIngestionError,
    ingest_seller_discovery_hit,
)
from core.services.seller_discovery_providers.base import (
    DiscoveryProvider,
    DiscoveryProviderError,
    build_discovery_provider,
)
from core.kazakhstan_locations import canonical_kazakhstan_city
from core.services.seller_discovery_providers.catalog import (
    SEARCH_DIRECTIONS,
    TWO_GIS_MAX_PAGES_CAP,
    resolve_city,
)
from core.services.seller_lead_search import SellerLeadSearchError

DEFAULT_MAX_QUERIES = 3
MAX_QUERIES_CAP = 20
DEFAULT_DIRECTIONS_PER_CITY = 3
DEFAULT_LIMIT = 5
MAX_LIMIT_CAP = 20
DEFAULT_MAX_HITS = 20
MAX_HITS_CAP = 50


class SellerDiscoveryRunError(ValueError):
    pass


@dataclass
class DiscoveryRunStats:
    dry_run: bool
    providers: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)
    queries_executed: int = 0
    hits_received: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    possible_duplicates: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)
    outcomes: list[IngestionResult] = field(default_factory=list)
    cities_processed: list[str] = field(default_factory=list)
    cities_selected: list[str] = field(default_factory=list)
    cities_completed: list[str] = field(default_factory=list)
    city_offset: int = 0
    city_limit: int | None = None
    next_city_offset: int = 0
    query_offset: int = 0
    next_query_offset: int = 0
    queries_per_city: int = 0
    classified: dict = field(default_factory=dict)
    new_parts: int = 0
    dismantlers: int = 0
    mixed: int = 0
    unknown: int = 0


def run_seller_discovery(
    *,
    provider_names: list[str] | tuple[str, ...],
    cities: list[str] | tuple[str, ...],
    directions: list[str] | tuple[str, ...] | None = None,
    limit: int | None = None,
    max_hits: int = DEFAULT_MAX_HITS,
    max_queries: int | None = None,
    max_pages: int | None = None,
    dry_run: bool = True,
    providers: list[DiscoveryProvider] | None = None,
    city_limit: int | None = None,
    city_offset: int | None = None,
    query_offset: int | None = None,
) -> DiscoveryRunStats:
    """Search providers and ingest hits. dry_run does not write SellerLead rows.

    max_hits is a boundary between query units, not inside one provider.search().
    A query that already started is ingested fully, so the last unit may pass
    the hit budget by the size of that bounded provider response. The next unit
    is not started. Resume with city_offset and query_offset together.
    """
    active = list(providers) if providers is not None else [
        build_discovery_provider(name) for name in provider_names
    ]
    resolved_cities = _resolve_cities(cities, providers=active)
    selected_cities, applied_limit, applied_offset = _select_city_batch(
        resolved_cities,
        city_limit=city_limit,
        city_offset=city_offset,
    )
    selected_directions = _resolve_directions(directions)
    page_size = None if limit is None else _bounded(limit, default=DEFAULT_LIMIT, cap=MAX_LIMIT_CAP, label='limit')
    hit_cap = _bounded(max_hits, default=DEFAULT_MAX_HITS, cap=MAX_HITS_CAP, label='max_hits')
    page_cap = None if max_pages is None else _bounded(
        max_pages,
        default=1,
        cap=TWO_GIS_MAX_PAGES_CAP,
        label='max_pages',
    )
    if not active:
        raise SellerDiscoveryRunError('Нужно указать хотя бы один источник: two_gis или brave.')
    work_units = [
        (direction, provider)
        for direction in selected_directions
        for provider in active
    ]
    queries_per_city = len(work_units)
    applied_query_offset = _resolve_query_offset(
        query_offset,
        queries_per_city,
        has_cities=bool(selected_cities),
    )
    if selected_cities:
        remaining_units = (
            (queries_per_city - applied_query_offset)
            + (len(selected_cities) - 1) * queries_per_city
        )
    else:
        remaining_units = 0
    if max_queries is None:
        query_cap = min(remaining_units, MAX_QUERIES_CAP)
    else:
        query_cap = min(
            _bounded(
                max_queries,
                default=DEFAULT_MAX_QUERIES,
                cap=MAX_QUERIES_CAP,
                label='max_queries',
            ),
            remaining_units,
        )

    stats = DiscoveryRunStats(
        dry_run=dry_run,
        providers=[provider.name for provider in active],
        cities=[city.name for city in selected_cities],
        directions=selected_directions,
        city_offset=applied_offset,
        city_limit=applied_limit,
        query_offset=applied_query_offset,
        queries_per_city=queries_per_city,
    )
    seen_match_ids: set[int] = set()
    queries_used = 0
    completed_names: list[str] = []
    next_query_offset = 0
    resume_at = applied_query_offset

    try:
        for city in selected_cities:
            start_unit = resume_at
            resume_at = 0
            finished_city = True
            for unit_index in range(start_unit, queries_per_city):
                if queries_used >= query_cap or stats.hits_received >= hit_cap:
                    finished_city = False
                    next_query_offset = unit_index
                    break
                direction, provider = work_units[unit_index]
                if city.name not in stats.cities_processed:
                    stats.cities_processed.append(city.name)
                queries_used += 1
                try:
                    hits = provider.search(
                        city=city.name,
                        direction=direction,
                        limit=page_size,
                        max_pages=page_cap,
                    )
                except (DiscoveryProviderError, SellerLeadSearchError) as exc:
                    stats.errors += 1
                    stats.error_messages.append(str(exc)[:300])
                    stats.queries_executed += 1
                    continue
                stats.queries_executed += 1
                for hit in hits:
                    stats.hits_received += 1
                    try:
                        outcome = ingest_seller_discovery_hit(hit, dry_run=dry_run)
                    except SellerDiscoveryIngestionError as exc:
                        stats.errors += 1
                        stats.error_messages.append(str(exc)[:300])
                        continue
                    stats.outcomes.append(outcome)
                    if outcome.seller_lead_id and outcome.business_type:
                        stats.classified[outcome.seller_lead_id] = outcome.business_type
                    if outcome.action == 'create':
                        stats.created += 1
                    elif outcome.action == 'update':
                        stats.updated += 1
                    else:
                        stats.skipped += 1
                    for match_id in outcome.possible_duplicate_ids:
                        seen_match_ids.add(match_id)
            if finished_city:
                completed_names.append(city.name)
                next_query_offset = 0
            else:
                break
        return stats
    finally:
        stats.cities_selected = [city.name for city in selected_cities]
        stats.cities = list(stats.cities_selected)
        stats.cities_completed = list(completed_names)
        stats.next_city_offset = applied_offset + len(completed_names)
        stats.next_query_offset = next_query_offset
        stats.queries_per_city = queries_per_city
        stats.query_offset = applied_query_offset
        stats.possible_duplicates = len(seen_match_ids)
        stats.new_parts = sum(1 for value in stats.classified.values() if value == 'new_parts')
        stats.dismantlers = sum(1 for value in stats.classified.values() if value == 'dismantler')
        stats.mixed = sum(1 for value in stats.classified.values() if value == 'mixed')
        stats.unknown = sum(1 for value in stats.classified.values() if value == 'unknown')


def _select_city_batch(cities, *, city_limit, city_offset):
    """Return a stable slice. Offset past the list raises before any provider call."""
    offset = 0 if city_offset is None else city_offset
    try:
        offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise SellerDiscoveryRunError('city-offset должен быть целым числом.') from exc
    if offset < 0:
        raise SellerDiscoveryRunError('city-offset должен быть больше или равен 0.')
    if offset > len(cities):
        raise SellerDiscoveryRunError(
            f'city-offset {offset} за пределами списка ({len(cities)} городов). Запросы не выполняются.',
        )
    if city_limit is None:
        return cities[offset:], None, offset
    limit = _bounded(city_limit, default=3, cap=5, label='city_limit')
    return cities[offset:offset + limit], limit, offset


@dataclass(frozen=True)
class DiscoveryRunCity:
    name: str


def _resolve_cities(
    cities: list[str] | tuple[str, ...],
    *,
    providers: list[DiscoveryProvider],
):
    if not cities:
        raise SellerDiscoveryRunError('Укажите хотя бы один город Казахстана.')

    requires_geo = any(getattr(provider, 'name', '') == 'two_gis' for provider in providers)
    resolved = []
    seen: set[str] = set()

    for name in cities:
        if requires_geo:
            city_name = resolve_city(name).name
        else:
            city_name = canonical_kazakhstan_city(name)
            if not city_name:
                raise SellerDiscoveryRunError(
                    f'Город {name} не входит в справочник городов Казахстана.'
                )
        if city_name in seen:
            continue
        seen.add(city_name)
        resolved.append(DiscoveryRunCity(name=city_name))
    return resolved


def _resolve_query_offset(query_offset, queries_per_city: int, *, has_cities: bool) -> int:
    offset = 0 if query_offset is None else query_offset
    try:
        offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise SellerDiscoveryRunError('query-offset должен быть целым числом. Запросы не выполняются.') from exc
    if offset < 0:
        raise SellerDiscoveryRunError('query-offset должен быть больше или равен 0. Запросы не выполняются.')
    if has_cities and offset != 0 and offset >= queries_per_city:
        raise SellerDiscoveryRunError(
            f'query-offset {offset} не меньше числа запросов на город ({queries_per_city}). '
            'Запросы не выполняются.',
        )
    return offset


def _resolve_directions(
    directions: list[str] | tuple[str, ...] | None,
) -> list[str]:
    if directions:
        cleaned = []
        for direction in directions:
            text = ' '.join(str(direction or '').split())
            if not text:
                continue
            cleaned.append(text[:120])
        if not cleaned:
            raise SellerDiscoveryRunError('Поисковое направление пустое.')
        return cleaned
    return list(SEARCH_DIRECTIONS[:DEFAULT_DIRECTIONS_PER_CITY])


def _bounded(value: int, *, default: int, cap: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SellerDiscoveryRunError(f'{label} должен быть целым числом.') from exc
    if number < 1:
        return default
    return min(number, cap)
