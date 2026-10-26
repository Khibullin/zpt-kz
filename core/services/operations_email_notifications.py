from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.db import close_old_connections, connections
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from core.models import InstagramPublication, Request, SellerLead, SellerLeadPipelineRun
from core.services.seller_lead_pipeline_email import get_pipeline_notification_email

logger = logging.getLogger(__name__)

MAX_ITEMS_IN_EMAIL = 20


def _admin_url(name: str, *args: Any) -> str | None:
    try:
        path = reverse(name, args=args)
    except NoReverseMatch:
        return None
    return f"{getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')}{path}"


def _enabled() -> bool:
    return bool(getattr(settings, 'SELLER_PIPELINE_EMAIL_ENABLED', False))


def _from_email() -> str:
    return getattr(settings, 'DEFAULT_FROM_EMAIL', None) or settings.EMAIL_HOST_USER


def _request_summary(request: Request) -> str:
    vehicle = ' '.join(part for part in (request.brand, request.model) if part).strip() or 'Автомобиль не указан'
    item = request.category or request.article or 'Запчасть не указана'
    if request.article and request.category:
        item = f'{request.category}, артикул {request.article}'
    city = request.city or 'Город не указан'
    return f'Заявка #{request.pk}: {vehicle}; {item}; {city}; статус: {request.status or "—"}'


def notify_new_request(request_id: int, *, service: bool = False) -> bool:
    """Email the owner a new committed request for manual WhatsApp processing."""
    if not getattr(settings, 'REQUEST_OWNER_EMAIL_ENABLED', False):
        return False
    recipient = getattr(settings, 'REQUEST_OWNER_EMAIL', '').strip()
    if not recipient:
        return False
    try:
        if service:
            from service_requests.models import ServiceRequest
            request = ServiceRequest.objects.filter(pk=request_id).first()
        else:
            request = Request.objects.filter(pk=request_id).first()
        if request is None:
            return False
        direction = (request.get_service_type_display() if service else
                     ('Авторазборы / б/у запчасти' if request.is_dismantler_request else 'Новые запчасти'))
        lines = [
            'ZPT.KZ — заявка для ручной обработки',
            'Текст для пересылки в WhatsApp:', '',
            f'Заявка #{request.pk} — {direction}',
            f'Город: {request.city}',
            f'Автомобиль: {request.brand} {request.model}',
            f'Тип транспорта: {request.get_transport_type_display()}',
        ]
        if service:
            lines.extend([f'Район: {request.district}',
                          'Услуги: ' + ', '.join(request.services.values_list('name', flat=True))])
        else:
            lines.extend([f'Категория: {request.category}', f'Артикул: {request.article}',
                          f'Год: {request.year or ""}', f'VIN: {request.vin}',
                          f'Территория поиска: {request.get_search_scope_display()}',
                          f'Выбранные города: {request.selected_cities}'])
        lines.extend([f'Описание: {request.description}', f'Телефон клиента: {request.phone}'])
        if not service:
            for photo in request.photos.all():
                url = photo.image.url
                if not url.startswith(('https://', 'http://')):
                    url = getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/') + '/' + url.lstrip('/')
                lines.append(f'Фото: {url}')
        admin_name = 'admin:service_requests_servicerequest_change' if service else 'control_panel:parts_request_process'
        request_url = _admin_url(admin_name, request.pk)
        if request_url:
            lines.extend(['', 'Для владельца — открыть заявку:', request_url])
        return bool(send_mail(
            subject=f'ZPT.KZ: {direction} — заявка #{request.pk}',
            message='\n'.join(lines), from_email=_from_email(),
            recipient_list=[recipient], fail_silently=False,
        ))
    except Exception:
        logger.exception('Owner request email failed: request=%s service=%s', request_id, service)
        return False


_owner_email_executor = None
_owner_email_lock = Lock()


def _get_owner_email_executor():
    global _owner_email_executor
    with _owner_email_lock:
        if _owner_email_executor is None:
            # Non-daemon executor workers finish pending mail on normal shutdown.
            _owner_email_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='owner-email')
    return _owner_email_executor


def _owner_email_job(request_id, service):
    close_old_connections()
    try:
        return notify_new_request(request_id, service=service)
    finally:
        connections.close_all()


def notify_new_request_async(request_id: int, *, service: bool = False) -> bool:
    """Enqueue only after commit; SMTP must not hold the buyer's HTTP response."""
    if not getattr(settings, 'REQUEST_OWNER_EMAIL_ENABLED', False):
        return False
    if not getattr(settings, 'REQUEST_OWNER_EMAIL_ASYNC', True):
        return notify_new_request(request_id, service=service)
    try:
        _get_owner_email_executor().submit(_owner_email_job, request_id, service)
        return True
    except Exception:
        logger.exception('Owner email enqueue failed: request=%s service=%s', request_id, service)
        return False


def build_daily_operations_email(*, now=None) -> str:
    """Build a summary of seller discovery, Instagram publications, and buyer requests for 24h."""
    moment = now or timezone.now()
    since = moment - timedelta(days=1)

    leads = SellerLead.objects.filter(created_at__gte=since, created_at__lte=moment).order_by('-created_at')
    requests = Request.objects.filter(created_at__gte=since, created_at__lte=moment).order_by('-created_at')
    publications = InstagramPublication.objects.filter(
        Q(created_at__gte=since, created_at__lte=moment)
        | Q(published_at__gte=since, published_at__lte=moment)
        | Q(last_attempt_at__gte=since, last_attempt_at__lte=moment)
    ).select_related('request').order_by('-created_at')
    runs = SellerLeadPipelineRun.objects.filter(
        trigger=SellerLeadPipelineRun.TRIGGER_CRON,
        is_dry_run=False,
        started_at__gte=since,
        started_at__lte=moment,
    ).order_by('-started_at')

    lines = [
        'ZPT.KZ — утренняя сводка за последние 24 часа',
        f'Период: {timezone.localtime(since):%d.%m.%Y %H:%M} — {timezone.localtime(moment):%d.%m.%Y %H:%M}',
        '',
        'ПОИСК ПРОДАВЦОВ',
        f'Новых профилей: {leads.count()}',
    ]
    by_source = leads.values('source_type').annotate(total=Count('pk')).order_by('source_type')
    if by_source:
        source_labels = dict(SellerLead._meta.get_field('source_type').choices)
        lines.extend(
            f'  {source_labels.get(item["source_type"], item["source_type"] or "Источник не указан")}: {item["total"]}'
            for item in by_source
        )
    lines.append(f'Запусков поискового pipeline: {runs.count()}')
    for run in runs[:MAX_ITEMS_IN_EMAIL]:
        stats = run.discovery_stats or {}
        found = stats.get('new_profiles', 0)
        lines.append(
            f'  {run.started_at:%d.%m %H:%M} — {run.get_status_display()}: '
            f'{run.search_term or run.category or "поиск"}, новых профилей: {found}'
        )
    for lead in leads[:MAX_ITEMS_IN_EMAIL]:
        profile_url = lead.instagram_url or lead.website_url or ''
        identity = f'@{lead.instagram_username}' if lead.instagram_username else lead.name
        lines.append(f'  {identity} — {lead.city or "город не указан"}{": " + profile_url if profile_url else ""}')
    if not leads.exists() and not runs.exists():
        lines.append('За этот период новых продавцов не найдено.')

    lines.extend(['', 'ПУБЛИКАЦИИ В INSTAGRAM'])
    if publications.exists():
        status_labels = dict(InstagramPublication.STATUS_CHOICES)
        counts = publications.values('status').annotate(total=Count('pk')).order_by('status')
        lines.extend(f'  {status_labels.get(row["status"], row["status"])}: {row["total"]}' for row in counts)
        for publication in publications[:MAX_ITEMS_IN_EMAIL]:
            lines.append(
                f'  Публикация #{publication.pk} по заявке #{publication.request_id}: '
                f'{publication.get_placement_display()} — {publication.get_status_display()}'
            )
    else:
        lines.append('За этот период публикаций и изменений статуса не было.')

    lines.extend(['', 'ЗАЯВКИ ПОКУПАТЕЛЕЙ', f'Новых заявок: {requests.count()}'])
    for request in requests[:MAX_ITEMS_IN_EMAIL]:
        lines.append(f'  {_request_summary(request)}')
    if not requests.exists():
        lines.append('За этот период новых заявок не поступало.')

    if leads.count() > MAX_ITEMS_IN_EMAIL or requests.count() > MAX_ITEMS_IN_EMAIL:
        lines.extend(['', 'В письме показаны первые 20 записей в каждом разделе.'])
    leads_url = _admin_url('admin:core_sellerlead_changelist')
    requests_url = _admin_url('admin:core_request_changelist')
    publications_url = _admin_url('admin:core_instagrampublication_changelist')
    lines.extend(['', 'Открыть в панели управления:'])
    if leads_url:
        lines.append(f'Профили продавцов: {leads_url}')
    if requests_url:
        lines.append(f'Заявки: {requests_url}')
    if publications_url:
        lines.append(f'Публикации Instagram: {publications_url}')
    return '\n'.join(lines)


def send_daily_operations_email(*, now=None) -> bool:
    if not _enabled():
        logger.info('Daily operations email skipped: SELLER_PIPELINE_EMAIL_ENABLED is false')
        return False
    recipient = get_pipeline_notification_email()
    if not recipient:
        logger.warning('Daily operations email skipped: no recipient')
        return False
    moment = now or timezone.now()
    try:
        send_mail(
            subject=f'ZPT.KZ: утренняя сводка — {timezone.localtime(moment):%d.%m.%Y}',
            message=build_daily_operations_email(now=moment),
            from_email=_from_email(),
            recipient_list=[recipient],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Daily operations email failed')
        return False
    logger.info('Daily operations email sent')
    return True
