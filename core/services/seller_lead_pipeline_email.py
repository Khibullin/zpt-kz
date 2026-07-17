from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Count
from django.urls import NoReverseMatch, reverse

from core.models import SellerLead, SellerLeadContactCandidate, SellerLeadPipelineRun
from core.services.seller_lead_pipeline_guard import truncate_safe_message
from core.services.seller_lead_pipeline_journal import journal_discovery_new_profiles
from core.services.seller_lead_search_rotation import SEARCH_ROTATION_PROFILES

logger = logging.getLogger(__name__)

MAX_LEADS_IN_EMAIL = 20

NOTIFIABLE_STATUSES = frozenset({
    SellerLeadPipelineRun.STATUS_SUCCESS,
    SellerLeadPipelineRun.STATUS_PARTIAL,
    SellerLeadPipelineRun.STATUS_FAILED,
    SellerLeadPipelineRun.STATUS_SKIPPED,
})

STATUS_LABELS = {
    SellerLeadPipelineRun.STATUS_SUCCESS: 'Успешно',
    SellerLeadPipelineRun.STATUS_PARTIAL: 'Завершён с предупреждениями',
    SellerLeadPipelineRun.STATUS_FAILED: 'Ошибка',
    SellerLeadPipelineRun.STATUS_SKIPPED: 'Пропущен',
}

TRIGGER_LABELS = {
    SellerLeadPipelineRun.TRIGGER_CRON: 'Cron',
    SellerLeadPipelineRun.TRIGGER_MANUAL: 'Ручной',
}


def mask_email(email: str) -> str:
    value = str(email or '').strip()
    if '@' not in value:
        return '***'
    local, domain = value.split('@', 1)
    if not local:
        return f'***@{domain}'
    return f'{local[0]}***@{domain}'


def get_pipeline_notification_email() -> str:
    for candidate in (
        getattr(settings, 'SELLER_PIPELINE_NOTIFICATION_EMAIL', ''),
        getattr(settings, 'ORDER_ADMIN_EMAIL', ''),
        getattr(settings, 'EMAIL_HOST_USER', ''),
    ):
        email = str(candidate or '').strip()
        if email:
            return email
    return ''


def should_send_pipeline_email(run: SellerLeadPipelineRun) -> bool:
    if not getattr(settings, 'SELLER_PIPELINE_EMAIL_ENABLED', False):
        return False
    if run.trigger != SellerLeadPipelineRun.TRIGGER_CRON:
        return False
    if run.is_dry_run:
        return False
    if run.status not in NOTIFIABLE_STATUSES:
        return False
    if not get_pipeline_notification_email():
        return False
    return True


def _search_term_label(run: SellerLeadPipelineRun) -> str:
    return (run.search_term or run.category or '—').strip() or '—'


def _new_sellers_phrase(count: int) -> str:
    if count == 0:
        return 'новых продавцов нет'
    remainder_100 = count % 100
    remainder_10 = count % 10
    if 11 <= remainder_100 <= 14:
        suffix = 'новых продавцов'
    elif remainder_10 == 1:
        suffix = 'новый продавец'
    elif 2 <= remainder_10 <= 4:
        suffix = 'новых продавца'
    else:
        suffix = 'новых продавцов'
    return f'найдено {count} {suffix}'


def build_pipeline_email_subject(run: SellerLeadPipelineRun) -> str:
    search_term = _search_term_label(run)
    if run.status == SellerLeadPipelineRun.STATUS_SUCCESS:
        new_count = journal_discovery_new_profiles(run)
        if new_count:
            return f'ZPT.KZ: {_new_sellers_phrase(new_count)} — {search_term}'
        return f'ZPT.KZ: новых продавцов нет — {search_term}'
    if run.status == SellerLeadPipelineRun.STATUS_PARTIAL:
        return f'ZPT.KZ: pipeline завершён с предупреждениями — {search_term}'
    if run.status == SellerLeadPipelineRun.STATUS_FAILED:
        return f'ZPT.KZ: ошибка SellerLead pipeline — {search_term}'
    if run.status == SellerLeadPipelineRun.STATUS_SKIPPED:
        return 'ZPT.KZ: SellerLead pipeline пропущен — cooldown'
    return f'ZPT.KZ: SellerLead pipeline — {search_term}'


def _admin_url(name: str, *args: Any) -> str | None:
    try:
        path = reverse(name, args=args)
    except NoReverseMatch:
        return None
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')
    return f'{base}{path}'


def _format_started_at(run: SellerLeadPipelineRun) -> str:
    if run.started_at:
        return run.started_at.strftime('%d.%m.%Y %H:%M')
    return '—'


def _rotation_position_label(run: SellerLeadPipelineRun) -> str:
    if not run.rotation_enabled:
        return '—'
    position = (run.rotation_index or 0) + 1
    total = len(SEARCH_ROTATION_PROFILES)
    return f'{position}/{total}'


def _safe_error_message(message: str) -> str:
    text = truncate_safe_message(message or '—')
    password = (getattr(settings, 'EMAIL_HOST_PASSWORD', '') or '').strip()
    if password:
        text = text.replace(password, '[REDACTED]')
    return text


def _stat_value(stats: dict[str, Any], key: str, default: int = 0) -> int:
    value = stats.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_discovery_block(run: SellerLeadPipelineRun) -> list[str]:
    stats = run.discovery_stats or {}
    if stats.get('skipped'):
        return ['DISCOVERY', 'Пропущен (--skip-discovery)']
    return [
        'DISCOVERY',
        f'Запросов: {_stat_value(stats, "queries_executed")}',
        f'Результатов Brave: {_stat_value(stats, "results_count")}',
        f'Распознано профилей: {_stat_value(stats, "recognized_profiles")}',
        f'Новых профилей: {_stat_value(stats, "new_profiles")}',
        f'Пропущено дублей: {_stat_value(stats, "skipped_duplicates")}',
        f'Отклонено ссылок: {_stat_value(stats, "rejected_links")}',
        f'Ошибок: {_stat_value(stats, "errors")}',
    ]


def _format_enrichment_block(run: SellerLeadPipelineRun) -> list[str]:
    stats = run.enrichment_stats or {}
    if stats.get('skipped'):
        return ['ENRICHMENT', 'Пропущен (--skip-enrichment)']
    return [
        'ENRICHMENT',
        f'Обработано лидов: {_stat_value(stats, "processed_leads")}',
        f'Поисковых запросов: {_stat_value(stats, "queries_executed")}',
        f'Найдено номеров: {_stat_value(stats, "found_numbers")}',
        f'Сохранено WhatsApp: {_stat_value(stats, "saved_primary_whatsapp")}',
        f'Конфликтов: {_stat_value(stats, "conflicts")}',
        f'Без контакта: {_stat_value(stats, "without_contact")}',
        f'Ошибок: {_stat_value(stats, "errors")}',
    ]


def _format_duration(run: SellerLeadPipelineRun) -> str:
    if not run.finished_at:
        return '—'
    total_seconds = int((run.finished_at - run.started_at).total_seconds())
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f'{minutes} мин. {seconds} сек.'
    return f'{seconds} сек.'


def _format_lead_line(lead: SellerLead, conflict_count: int) -> str:
    username = lead.instagram_username or '—'
    if lead.whatsapp:
        whatsapp_label = f'WhatsApp: {lead.whatsapp}'
    else:
        whatsapp_label = 'WhatsApp не найден'
    line = f'- @{username} — {whatsapp_label}'
    meta_parts: list[str] = []
    if lead.city:
        meta_parts.append(lead.city)
    if lead.category:
        meta_parts.append(lead.category)
    if lead.whatsapp_confidence:
        meta_parts.append(f'уверенность: {lead.whatsapp_confidence}')
    if conflict_count:
        meta_parts.append(f'конфликтов: {conflict_count}')
    if meta_parts:
        line = f'{line} ({", ".join(meta_parts)})'
    return line


def _format_new_leads_block(run: SellerLeadPipelineRun) -> list[str]:
    if run.status not in (
        SellerLeadPipelineRun.STATUS_SUCCESS,
        SellerLeadPipelineRun.STATUS_PARTIAL,
    ):
        return []

    lead_ids = list(run.created_lead_ids or [])[:MAX_LEADS_IN_EMAIL]
    if not lead_ids:
        return []

    leads = list(
        SellerLead.objects.filter(id__in=lead_ids).order_by('id'),
    )
    conflict_counts = {
        row['seller_lead_id']: row['count']
        for row in SellerLeadContactCandidate.objects.filter(
            seller_lead_id__in=lead_ids,
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        ).values('seller_lead_id').annotate(count=Count('id'))
    }
    lines = ['', 'Новые профили:']
    for lead in leads:
        lines.append(
            _format_lead_line(
                lead,
                conflict_counts.get(lead.pk, 0),
            ),
        )
    omitted = len(run.created_lead_ids or []) - len(lead_ids)
    if omitted > 0:
        lines.append(f'... и ещё {omitted} профилей (см. журнал)')
    return lines


def build_pipeline_email_body(run: SellerLeadPipelineRun) -> str:
    lines = [
        'ZPT.KZ — отчёт поиска потенциальных продавцов',
        '',
        f'Статус: {STATUS_LABELS.get(run.status, run.status)}',
        f'Источник запуска: {TRIGGER_LABELS.get(run.trigger, run.trigger)}',
        f'Run UUID: {run.run_uuid}',
        f'Дата начала: {_format_started_at(run)}',
        f'Продолжительность: {_format_duration(run)}',
        f'Город: {run.city or "—"}',
        f'Поисковое направление: {_search_term_label(run)}',
        f'Категория: {run.category or "—"}',
        f'Профиль ротации: {run.rotation_slug or "—"}',
        f'Позиция ротации: {_rotation_position_label(run)}',
        '',
    ]

    if run.status == SellerLeadPipelineRun.STATUS_SKIPPED:
        lines.extend([
            f'Причина пропуска: {run.skip_reason or "—"}',
        ])
        return '\n'.join(lines)

    if run.status == SellerLeadPipelineRun.STATUS_FAILED:
        safe_error = _safe_error_message(run.error_message or '—')
        lines.extend([
            f'Сообщение об ошибке: {safe_error}',
            '',
        ])
    else:
        lines.extend(_format_discovery_block(run))
        lines.append('')
        lines.extend(_format_enrichment_block(run))
        lines.extend(_format_new_leads_block(run))

    run_url = _admin_url('admin:core_sellerleadpipelinerun_change', run.pk)
    leads_url = _admin_url('admin:core_sellerlead_changelist')
    lines.append('')
    if run_url:
        lines.append('Ссылка на журнал:')
        lines.append(run_url)
    if leads_url:
        lines.append('Список потенциальных продавцов:')
        lines.append(leads_url)

    return '\n'.join(lines)


def send_pipeline_run_notification(run: SellerLeadPipelineRun) -> bool:
    recipient = get_pipeline_notification_email()
    if not recipient:
        logger.warning(
            'Seller lead pipeline email skipped: no recipient, run=%s',
            run.run_uuid,
        )
        return False

    subject = build_pipeline_email_subject(run)
    body = build_pipeline_email_body(run)
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or settings.EMAIL_HOST_USER

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )
    except Exception as exc:
        logger.error(
            'Seller lead pipeline email failed: run=%s, error=%s',
            run.run_uuid,
            truncate_safe_message(str(exc)),
        )
        return False

    logger.info(
        'Seller lead pipeline email sent: run=%s, status=%s, recipient=%s',
        run.run_uuid,
        run.status,
        mask_email(recipient),
    )
    return True


def notify_pipeline_run_safely(run: SellerLeadPipelineRun) -> bool:
    if not should_send_pipeline_email(run):
        return False
    try:
        return send_pipeline_run_notification(run)
    except Exception as exc:
        logger.error(
            'Seller lead pipeline email failed: run=%s, error=%s',
            run.run_uuid,
            truncate_safe_message(str(exc)),
        )
        return False
