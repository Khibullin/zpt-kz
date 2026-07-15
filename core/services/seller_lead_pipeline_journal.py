from __future__ import annotations

from typing import Any

from django.utils import timezone

from core.models import SellerLeadPipelineRun
from core.services.seller_lead_pipeline import (
    PipelineDiscoveryStats,
    PipelineEnrichmentStats,
    SellerLeadPipelineStats,
)
from core.services.seller_lead_pipeline_guard import truncate_safe_message


def discovery_stats_to_journal(discovery: PipelineDiscoveryStats) -> dict[str, int | bool]:
    return {
        'queries_executed': discovery.queries_executed,
        'results_count': discovery.results_found,
        'recognized_profiles': discovery.profiles_parsed,
        'new_profiles': discovery.new_profiles,
        'skipped_duplicates': discovery.duplicates_skipped,
        'rejected_links': discovery.links_rejected,
        'errors': discovery.errors,
        'skipped': discovery.skipped,
    }


def enrichment_stats_to_journal(enrichment: PipelineEnrichmentStats) -> dict[str, int | bool]:
    return {
        'processed_leads': enrichment.leads_processed,
        'queries_executed': enrichment.queries_executed,
        'found_numbers': enrichment.candidates_found,
        'high': enrichment.high_confidence,
        'medium': enrichment.medium_confidence,
        'low': enrichment.low_confidence,
        'conflicts': enrichment.conflicts,
        'saved_primary_whatsapp': enrichment.saved_primary,
        'created_candidates': enrichment.candidates_created,
        'updated_candidates': enrichment.candidates_updated,
        'without_contact': enrichment.no_contact,
        'errors': enrichment.errors,
        'skipped': enrichment.skipped,
    }


def determine_final_status(stats: SellerLeadPipelineStats) -> str:
    discovery_errors = stats.discovery.errors
    enrichment_errors = stats.enrichment.errors
    if discovery_errors or enrichment_errors:
        return SellerLeadPipelineRun.STATUS_PARTIAL
    return SellerLeadPipelineRun.STATUS_SUCCESS


from core.services.seller_lead_search_rotation import ResolvedPipelineSearch


def _rotation_fields(resolved_search: ResolvedPipelineSearch | None) -> dict[str, Any]:
    if resolved_search is None:
        return {
            'search_term': '',
            'rotation_enabled': False,
            'rotation_slug': '',
            'rotation_index': None,
        }
    return {
        'search_term': resolved_search.search_term,
        'rotation_enabled': resolved_search.rotation_enabled,
        'rotation_slug': resolved_search.rotation_slug,
        'rotation_index': resolved_search.rotation_index,
    }


def create_running_pipeline_run(
    *,
    trigger: str,
    city: str,
    category: str,
    search_limit: int,
    lead_limit: int,
    max_queries_per_lead: int,
    skip_discovery: bool,
    skip_enrichment: bool,
    cooldown_minutes: int,
    force_run: bool,
    resolved_search: ResolvedPipelineSearch | None = None,
) -> SellerLeadPipelineRun:
    return SellerLeadPipelineRun.objects.create(
        trigger=trigger,
        status=SellerLeadPipelineRun.STATUS_RUNNING,
        is_dry_run=False,
        city=city,
        category=category,
        search_limit=search_limit,
        lead_limit=lead_limit,
        max_queries_per_lead=max_queries_per_lead,
        skip_discovery=skip_discovery,
        skip_enrichment=skip_enrichment,
        cooldown_minutes=cooldown_minutes,
        force_run=force_run,
        started_at=timezone.now(),
        **_rotation_fields(resolved_search),
    )


def finalize_pipeline_run(
    run: SellerLeadPipelineRun,
    *,
    stats: SellerLeadPipelineStats,
    status: str | None = None,
    error_message: str = '',
) -> SellerLeadPipelineRun:
    run.discovery_stats = discovery_stats_to_journal(stats.discovery)
    run.enrichment_stats = enrichment_stats_to_journal(stats.enrichment)
    run.created_lead_ids = list(stats.created_lead_ids)
    run.status = status or determine_final_status(stats)
    run.finished_at = timezone.now()
    if error_message:
        run.error_message = truncate_safe_message(error_message)
    run.save(
        update_fields=[
            'discovery_stats',
            'enrichment_stats',
            'created_lead_ids',
            'status',
            'finished_at',
            'error_message',
        ],
    )
    return run


def create_skipped_pipeline_run(
    *,
    trigger: str,
    city: str,
    category: str,
    search_limit: int,
    lead_limit: int,
    max_queries_per_lead: int,
    skip_discovery: bool,
    skip_enrichment: bool,
    cooldown_minutes: int,
    force_run: bool,
    skip_reason: str,
    resolved_search: ResolvedPipelineSearch | None = None,
) -> SellerLeadPipelineRun:
    now = timezone.now()
    return SellerLeadPipelineRun.objects.create(
        trigger=trigger,
        status=SellerLeadPipelineRun.STATUS_SKIPPED,
        is_dry_run=False,
        city=city,
        category=category,
        search_limit=search_limit,
        lead_limit=lead_limit,
        max_queries_per_lead=max_queries_per_lead,
        skip_discovery=skip_discovery,
        skip_enrichment=skip_enrichment,
        cooldown_minutes=cooldown_minutes,
        force_run=force_run,
        started_at=now,
        finished_at=now,
        skip_reason=truncate_safe_message(skip_reason),
        **_rotation_fields(resolved_search),
    )


def mark_pipeline_run_failed(
    run: SellerLeadPipelineRun,
    *,
    error_message: str,
    stats: SellerLeadPipelineStats | None = None,
) -> SellerLeadPipelineRun:
    if stats is not None:
        run.discovery_stats = discovery_stats_to_journal(stats.discovery)
        run.enrichment_stats = enrichment_stats_to_journal(stats.enrichment)
        run.created_lead_ids = list(stats.created_lead_ids)
    run.status = SellerLeadPipelineRun.STATUS_FAILED
    run.finished_at = timezone.now()
    run.error_message = truncate_safe_message(error_message)
    run.save()
    return run


def journal_discovery_new_profiles(run: SellerLeadPipelineRun) -> int:
    return int((run.discovery_stats or {}).get('new_profiles', 0))


def journal_enrichment_saved_contacts(run: SellerLeadPipelineRun) -> int:
    return int((run.enrichment_stats or {}).get('saved_primary_whatsapp', 0))


def journal_enrichment_conflicts(run: SellerLeadPipelineRun) -> int:
    return int((run.enrichment_stats or {}).get('conflicts', 0))
