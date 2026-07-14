from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.models import SellerLead
from core.services.seller_lead_contact_search import (
    ContactEnrichmentStats,
    enrich_seller_lead_contacts,
)
from core.services.seller_lead_search import (
    InstagramProfileCandidate,
    SellerLeadCollectStats,
    SellerLeadSearchConfigError,
    collect_instagram_seller_leads,
    get_seller_search_settings,
)

DEFAULT_CITY = 'Алматы'
DEFAULT_CATEGORY = 'автозапчасти'
DEFAULT_SEARCH_LIMIT = 10
DEFAULT_LEAD_LIMIT = 3
DEFAULT_MAX_QUERIES_PER_LEAD = 3

MAX_SEARCH_LIMIT = 50
MAX_LEAD_LIMIT = 20
MAX_QUERIES_PER_LEAD_LIMIT = 5


class SellerLeadPipelineConfigError(SellerLeadSearchConfigError):
    """Ошибка конфигурации pipeline."""


@dataclass
class PipelineLeadReport:
    username: str
    phones: list[str] = field(default_factory=list)
    confidence: str = ''
    source_url: str = ''
    action: str = ''
    reason: str = ''


@dataclass
class PipelineDiscoveryStats:
    queries_executed: int = 0
    results_found: int = 0
    profiles_parsed: int = 0
    new_profiles: int = 0
    duplicates_skipped: int = 0
    links_rejected: int = 0
    errors: int = 0
    skipped: bool = False


@dataclass
class PipelineEnrichmentStats:
    leads_processed: int = 0
    queries_executed: int = 0
    candidates_found: int = 0
    high_confidence: int = 0
    medium_confidence: int = 0
    low_confidence: int = 0
    conflicts: int = 0
    saved_primary: int = 0
    candidates_created: int = 0
    candidates_updated: int = 0
    no_contact: int = 0
    errors: int = 0
    skipped: bool = False
    lead_reports: list[PipelineLeadReport] = field(default_factory=list)


@dataclass
class SellerLeadPipelineStats:
    dry_run: bool = False
    discovery: PipelineDiscoveryStats = field(default_factory=PipelineDiscoveryStats)
    enrichment: PipelineEnrichmentStats = field(default_factory=PipelineEnrichmentStats)


def validate_pipeline_limits(
    *,
    search_limit: int,
    lead_limit: int,
    max_queries_per_lead: int,
) -> None:
    if search_limit <= 0:
        raise SellerLeadPipelineConfigError('search-limit должен быть больше 0.')
    if lead_limit <= 0:
        raise SellerLeadPipelineConfigError('lead-limit должен быть больше 0.')
    if max_queries_per_lead <= 0:
        raise SellerLeadPipelineConfigError('max-queries-per-lead должен быть больше 0.')
    if search_limit > MAX_SEARCH_LIMIT:
        raise SellerLeadPipelineConfigError(
            f'search-limit не может превышать {MAX_SEARCH_LIMIT}.',
        )
    if lead_limit > MAX_LEAD_LIMIT:
        raise SellerLeadPipelineConfigError(
            f'lead-limit не может превышать {MAX_LEAD_LIMIT}.',
        )
    if max_queries_per_lead > MAX_QUERIES_PER_LEAD_LIMIT:
        raise SellerLeadPipelineConfigError(
            f'max-queries-per-lead не может превышать {MAX_QUERIES_PER_LEAD_LIMIT}.',
        )


def profile_to_lead_draft(profile: InstagramProfileCandidate) -> SellerLead:
    return SellerLead(
        name=(profile.title.strip() or profile.username)[:255],
        instagram_username=profile.username,
        instagram_url=profile.profile_url,
        city=profile.city,
        category=profile.category,
        profile_description=profile.description,
        source_url=profile.source_url,
        source_type='web_search',
        status=SellerLead.STATUS_NEEDS_REVIEW,
    )


def _discovery_stats_from_collect(stats: SellerLeadCollectStats, *, dry_run: bool) -> PipelineDiscoveryStats:
    return PipelineDiscoveryStats(
        queries_executed=stats.queries_executed,
        results_found=stats.results_found,
        profiles_parsed=stats.profiles_parsed,
        new_profiles=len(stats.dry_run_profiles) if dry_run else stats.created,
        duplicates_skipped=stats.duplicates_skipped,
        links_rejected=stats.links_rejected,
        errors=stats.errors,
    )


def _build_lead_reports(
    enrichment_stats: ContactEnrichmentStats,
    *,
    dry_run: bool,
) -> list[PipelineLeadReport]:
    conflict_phones_by_username: dict[str, list[str]] = {}
    conflict_reason_by_username: dict[str, str] = {}
    for outcome in enrichment_stats.conflict_outcomes:
        conflict_phones_by_username.setdefault(outcome.username, []).append(outcome.phone)
        conflict_reason_by_username[outcome.username] = outcome.reason

    reports: list[PipelineLeadReport] = []

    for outcome in enrichment_stats.lead_outcomes:
        username = outcome.username
        if username in conflict_phones_by_username:
            action = 'будет создан conflict-candidate' if dry_run else 'создан conflict-candidate'
            reports.append(
                PipelineLeadReport(
                    username=username,
                    phones=sorted(set(conflict_phones_by_username[username])),
                    confidence='mixed',
                    source_url=outcome.source_url,
                    action=action,
                    reason=conflict_reason_by_username.get(username, 'конфликт номеров'),
                ),
            )
            continue

        if outcome.accepted:
            action = 'будет сохранён основной' if dry_run else 'сохранён основной'
            reports.append(
                PipelineLeadReport(
                    username=username,
                    phones=[outcome.phone] if outcome.phone else [],
                    confidence=outcome.confidence,
                    source_url=outcome.source_url,
                    action=action,
                    reason=outcome.rejection_reason,
                ),
            )
            continue

        if outcome.rejection_reason == 'ошибка поискового API':
            reports.append(
                PipelineLeadReport(
                    username=username,
                    action='ошибка',
                    reason=outcome.rejection_reason,
                ),
            )
            continue

        reports.append(
            PipelineLeadReport(
                username=username,
                phones=[outcome.phone] if outcome.phone else [],
                confidence=outcome.confidence,
                source_url=outcome.source_url,
                action='контакт не найден',
                reason=outcome.rejection_reason or 'подходящий номер не найден',
            ),
        )

    return reports


def _enrichment_stats_from_contact(
    stats: ContactEnrichmentStats,
    *,
    dry_run: bool,
) -> PipelineEnrichmentStats:
    lead_reports = _build_lead_reports(stats, dry_run=dry_run)
    no_contact = sum(1 for report in lead_reports if report.action == 'контакт не найден')
    return PipelineEnrichmentStats(
        leads_processed=stats.leads_processed,
        queries_executed=stats.queries_executed,
        candidates_found=stats.candidates_found,
        high_confidence=stats.high_confidence,
        medium_confidence=stats.medium_confidence,
        low_confidence=stats.low_confidence,
        conflicts=stats.conflicts,
        saved_primary=stats.ready_to_save if dry_run else stats.saved,
        candidates_created=stats.contact_candidates_created,
        candidates_updated=stats.contact_candidates_updated,
        no_contact=no_contact,
        errors=stats.errors,
        lead_reports=lead_reports,
    )


def run_seller_lead_pipeline(
    *,
    city: str = DEFAULT_CITY,
    category: str = DEFAULT_CATEGORY,
    search_limit: int = DEFAULT_SEARCH_LIMIT,
    lead_limit: int = DEFAULT_LEAD_LIMIT,
    max_queries_per_lead: int = DEFAULT_MAX_QUERIES_PER_LEAD,
    dry_run: bool = False,
    skip_discovery: bool = False,
    skip_enrichment: bool = False,
    client: Any | None = None,
    search_settings: dict[str, Any] | None = None,
) -> SellerLeadPipelineStats:
    validate_pipeline_limits(
        search_limit=search_limit,
        lead_limit=lead_limit,
        max_queries_per_lead=max_queries_per_lead,
    )

    settings_data = search_settings or get_seller_search_settings()
    if settings_data.get('provider') != 'brave':
        raise SellerLeadPipelineConfigError(
            f"Неподдерживаемый SELLER_SEARCH_PROVIDER: {settings_data.get('provider')}",
        )
    if not settings_data.get('enabled'):
        raise SellerLeadPipelineConfigError(
            'SELLER_SEARCH_ENABLED=False. Реальные запросы к поисковому API отключены.',
        )
    if not settings_data.get('api_key'):
        raise SellerLeadPipelineConfigError(
            'BRAVE_SEARCH_API_KEY не задан. Укажите ключ в переменных окружения.',
        )

    pipeline_stats = SellerLeadPipelineStats(dry_run=dry_run)
    created_lead_ids: list[int] = []
    dry_run_profiles: list[InstagramProfileCandidate] = []

    if skip_discovery:
        pipeline_stats.discovery.skipped = True
    else:
        collect_stats = collect_instagram_seller_leads(
            city=city,
            category=category,
            limit=search_limit,
            max_new_leads=lead_limit,
            dry_run=dry_run,
            client=client,
            search_settings=settings_data,
        )
        pipeline_stats.discovery = _discovery_stats_from_collect(collect_stats, dry_run=dry_run)
        created_lead_ids = list(collect_stats.created_lead_ids[:lead_limit])
        dry_run_profiles = list(collect_stats.dry_run_profiles[:lead_limit])

    if skip_enrichment:
        pipeline_stats.enrichment.skipped = True
        return pipeline_stats

    if dry_run:
        enrichment_input = [profile_to_lead_draft(profile) for profile in dry_run_profiles]
        contact_stats = enrich_seller_lead_contacts(
            leads=enrichment_input,
            limit=lead_limit,
            max_queries_per_lead=max_queries_per_lead,
            dry_run=True,
            client=client,
            search_settings=settings_data,
        )
    elif created_lead_ids:
        contact_stats = enrich_seller_lead_contacts(
            lead_ids=created_lead_ids,
            limit=lead_limit,
            max_queries_per_lead=max_queries_per_lead,
            dry_run=False,
            client=client,
            search_settings=settings_data,
        )
    else:
        contact_stats = ContactEnrichmentStats()

    pipeline_stats.enrichment = _enrichment_stats_from_contact(contact_stats, dry_run=dry_run)
    return pipeline_stats
