from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from core.models import SellerLeadPipelineRun
from core.services.seller_lead_pipeline import (
    SellerLeadPipelineStats,
    run_seller_lead_pipeline,
)
from core.services.seller_lead_pipeline_guard import (
    CooldownCheckResult,
    PipelineLockBusy,
    PipelineRunLock,
    check_pipeline_cooldown,
    truncate_safe_message,
)
from core.services.seller_lead_pipeline_journal import (
    create_running_pipeline_run,
    create_skipped_pipeline_run,
    finalize_pipeline_run,
    mark_pipeline_run_failed,
)


@dataclass
class ManagedPipelineResult:
    stats: SellerLeadPipelineStats | None = None
    run: SellerLeadPipelineRun | None = None
    lock_busy: bool = False
    cooldown_blocked: bool = False
    cooldown_check: CooldownCheckResult | None = None


def execute_managed_seller_lead_pipeline(
    *,
    city: str,
    category: str,
    search_limit: int,
    lead_limit: int,
    max_queries_per_lead: int,
    skip_discovery: bool,
    skip_enrichment: bool,
    cooldown_minutes: int,
    force_run: bool,
    trigger: str,
    client: Any | None = None,
    search_settings: dict[str, Any] | None = None,
) -> ManagedPipelineResult:
    try:
        with PipelineRunLock():
            cooldown_check = check_pipeline_cooldown(
                cooldown_minutes=cooldown_minutes,
                force_run=force_run,
            )
            if not cooldown_check.allowed:
                previous = cooldown_check.previous_run
                skip_reason = (
                    f'Cooldown {cooldown_minutes} мин. не истёк. '
                    f'Предыдущий запуск {previous.run_uuid} от {previous.started_at:%Y-%m-%d %H:%M}. '
                    f'Осталось ~{cooldown_check.minutes_remaining} мин.'
                )
                skipped_run = create_skipped_pipeline_run(
                    trigger=trigger,
                    city=city,
                    category=category,
                    search_limit=search_limit,
                    lead_limit=lead_limit,
                    max_queries_per_lead=max_queries_per_lead,
                    skip_discovery=skip_discovery,
                    skip_enrichment=skip_enrichment,
                    cooldown_minutes=cooldown_minutes,
                    force_run=force_run,
                    skip_reason=skip_reason,
                )
                return ManagedPipelineResult(
                    run=skipped_run,
                    cooldown_blocked=True,
                    cooldown_check=cooldown_check,
                )

            run = create_running_pipeline_run(
                trigger=trigger,
                city=city,
                category=category,
                search_limit=search_limit,
                lead_limit=lead_limit,
                max_queries_per_lead=max_queries_per_lead,
                skip_discovery=skip_discovery,
                skip_enrichment=skip_enrichment,
                cooldown_minutes=cooldown_minutes,
                force_run=force_run,
            )
            try:
                stats = run_seller_lead_pipeline(
                    city=city,
                    category=category,
                    search_limit=search_limit,
                    lead_limit=lead_limit,
                    max_queries_per_lead=max_queries_per_lead,
                    dry_run=False,
                    skip_discovery=skip_discovery,
                    skip_enrichment=skip_enrichment,
                    client=client,
                    search_settings=search_settings,
                )
            except Exception as exc:
                safe_message = truncate_safe_message(str(exc))
                mark_pipeline_run_failed(run, error_message=safe_message)
                raise

            finalize_pipeline_run(run, stats=stats)
            return ManagedPipelineResult(stats=stats, run=run)
    except PipelineLockBusy:
        return ManagedPipelineResult(lock_busy=True)


def format_run_duration(run: SellerLeadPipelineRun) -> str:
    if not run.finished_at:
        return '—'
    delta: timedelta = run.finished_at - run.started_at
    total_seconds = int(delta.total_seconds())
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f'{minutes} мин {seconds} сек'
    return f'{seconds} сек'
