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
from core.services.seller_discovery_providers.catalog import (
    SEARCH_DIRECTIONS,
    TWO_GIS_MAX_PAGES_CAP,
    resolve_city,
)
from core.services.seller_lead_search import SellerLeadSearchError

DEFAULT_MAX_QUERIES = 3
MAX_QUERIES_CAP = 20
DEFAULT_LIMIT = 5
MAX_LIMIT_CAP = 10
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
    city_offset: int = 0
    city_limit: int | None = None
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
    max_queries: int = DEFAULT_MAX_QUERIES,
    max_pages: int | None = None,
    dry_run: bool = True,
    providers: list[DiscoveryProvider] | None = None,
    city_limit: int | None = None,
    city_offset: int | None = None,
) -> DiscoveryRunStats:
    """Search providers and ingest hits. dry_run does not write SellerLead rows."""
    resolved_cities = _resolve_cities(cities)
    selected_cities, applied_limit, applied_offset = _select_city_batch(
        resolved_cities,
        city_limit=city_limit,
        city_offset=city_offset,
    )
    selected_directions = _resolve_directions(directions, max_queries=max_queries)
    page_size = None if limit is None else _bounded(limit, default=DEFAULT_LIMIT, cap=MAX_LIMIT_CAP, label='limit')
    hit_cap = _bounded(max_hits, default=DEFAULT_MAX_HITS, cap=MAX_HITS_CAP, label='max_hits')
    query_cap = _bounded(max_queries, default=DEFAULT_MAX_QUERIES, cap=MAX_QUERIES_CAP, label='max_queries')
    page_cap = None if max_pages is None else _bounded(
        max_pages,
        default=1,
        cap=TWO_GIS_MAX_PAGES_CAP,
        label='max_pages',
    )
    active = list(providers) if providers is not None else [
        build_discovery_provider(name) for name in provider_names
    ]
    if not active:
        raise SellerDiscoveryRunError('Нужно указать хотя бы один источник: two_gis или brave.')

    stats = DiscoveryRunStats(
        dry_run=dry_run,
        providers=[provider.name for provider in active],
        cities=[city.name for city in selected_cities],
        directions=selected_directions,
        city_offset=applied_offset,
        city_limit=applied_limit,
    )
    seen_match_ids: set[int] = set()
    queries_used = 0

    try:
        for city in selected_cities:
            for direction in selected_directions:
                for provider in active:
                    if queries_used >= query_cap or stats.hits_received >= hit_cap:
                        return stats
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
                        if stats.hits_received >= hit_cap:
                            return stats
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
        return stats
    finally:
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


def _resolve_cities(cities: list[str] | tuple[str, ...]):
    if not cities:
        raise SellerDiscoveryRunError('Укажите город MVP или все три города явно.')
    resolved = []
    seen: set[str] = set()
    for name in cities:
        city = resolve_city(name)
        if city.name in seen:
            continue
        seen.add(city.name)
        resolved.append(city)
    return resolved


def _resolve_directions(
    directions: list[str] | tuple[str, ...] | None,
    *,
    max_queries: int,
) -> list[str]:
    cap = _bounded(max_queries, default=DEFAULT_MAX_QUERIES, cap=MAX_QUERIES_CAP, label='max_queries')
    if directions:
        cleaned = []
        for direction in directions:
            text = ' '.join(str(direction or '').split())
            if not text:
                continue
            cleaned.append(text[:120])
        if not cleaned:
            raise SellerDiscoveryRunError('Поисковое направление пустое.')
        return cleaned[:cap]
    return list(SEARCH_DIRECTIONS[:cap])


def _bounded(value: int, *, default: int, cap: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SellerDiscoveryRunError(f'{label} должен быть целым числом.') from exc
    if number < 1:
        return default
    return min(number, cap)
