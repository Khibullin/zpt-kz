"""
Оркестрация безопасной публикации заявок в Instagram Stories.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.db import IntegrityError
from django.db.models import Count
from django.utils import timezone

from catalog.image_generator import (
    InstagramStoryGenerationError,
    build_publication_caption,
    generate_instagram_story,
)
from catalog.instagram_api import (
    InstagramPublishError,
    absolute_media_path_to_relative,
    publish_story_to_instagram,
)
from core.instagram_sanitize import is_junk_only_description
from core.models import InstagramPublication, Request

logger = logging.getLogger(__name__)

STUCK_PUBLISHING_TIMEOUT = timedelta(minutes=5)


def get_instagram_publish_mode() -> str:
    mode = (getattr(settings, 'INSTAGRAM_PUBLISH_MODE', 'OFF') or 'OFF').strip().upper()
    if mode not in ('OFF', 'TEST', 'LIVE'):
        logger.warning('Неизвестный INSTAGRAM_PUBLISH_MODE=%s, используется OFF.', mode)
        return 'OFF'
    return mode


def schedule_instagram_publication_for_request(request_id: int) -> None:
    """
    Безопасная точка входа после commit транзакции создания заявки.
    Любые ошибки логируются и не пробрасываются наружу.
    """
    try:
        process_instagram_publication_for_request(request_id)
    except Exception:
        logger.exception(
            'Instagram pipeline failed for request #%s',
            request_id,
        )


def process_instagram_publication_for_request(request_id: int) -> InstagramPublication | None:
    mode = get_instagram_publish_mode()
    if mode == 'OFF':
        logger.debug('Instagram publish mode OFF — пропуск заявки #%s', request_id)
        return None

    existing = InstagramPublication.objects.filter(request_id=request_id).first()
    if existing:
        logger.info(
            'Instagram publication already exists for request #%s (status=%s)',
            request_id,
            existing.status,
        )
        if mode == 'LIVE' and existing.status in (
            InstagramPublication.STATUS_DRAFT,
            InstagramPublication.STATUS_FAILED,
        ):
            try:
                product_request = Request.objects.get(pk=request_id)
            except Request.DoesNotExist:
                return existing
            if not is_junk_only_description(product_request.description):
                queue_instagram_publication_for_processing(existing)
            return existing
        return existing

    try:
        product_request = Request.objects.get(pk=request_id)
    except Request.DoesNotExist:
        logger.warning('Request #%s not found for Instagram publication', request_id)
        return None

    junk_only_description = is_junk_only_description(product_request.description)
    if junk_only_description:
        logger.info(
            'Request #%s has junk-only description, Instagram publication will stay draft',
            request_id,
        )

    try:
        output_path, caption = generate_instagram_story(product_request)
    except InstagramStoryGenerationError as exc:
        logger.warning(
            'Instagram Story not generated for request #%s: %s',
            request_id,
            exc,
        )
        return None

    relative_path = absolute_media_path_to_relative(output_path)

    try:
        publication = InstagramPublication.objects.create(
            request=product_request,
            caption=caption,
            status=InstagramPublication.STATUS_DRAFT,
        )
    except IntegrityError:
        logger.info('Instagram publication race for request #%s', request_id)
        publication = InstagramPublication.objects.get(request_id=request_id)
        return publication

    publication.image.name = relative_path
    publication.save(update_fields=['image'])

    logger.info(
        'Instagram draft created for request #%s (publication #%s)',
        request_id,
        publication.pk,
    )

    if mode == 'LIVE' and not junk_only_description:
        queue_instagram_publication_for_processing(publication)
        return publication

    return publication


def queue_instagram_publication_for_processing(
    publication: InstagramPublication,
) -> InstagramPublication:
    """Ставит публикацию в очередь без синхронного вызова Meta API."""
    if publication.status == InstagramPublication.STATUS_PUBLISHED:
        return publication

    publication.status = InstagramPublication.STATUS_QUEUED
    publication.publishing_started_at = None
    publication.error_message = ''
    publication.save(update_fields=['status', 'publishing_started_at', 'error_message'])
    logger.info('Instagram publication #%s: queued', publication.pk)
    return publication


def get_instagram_publication_queue_diagnostics() -> dict:
    """Диагностика очереди Instagram-публикаций для cron / management-команды."""
    total = InstagramPublication.objects.count()
    status_counts = {
        status_value: 0
        for status_value, _label in InstagramPublication.STATUS_CHOICES
    }
    for row in InstagramPublication.objects.values('status').annotate(
        count=Count('id'),
    ):
        status_counts[row['status']] = row['count']

    recent = list(
        InstagramPublication.objects.order_by('-created_at').values(
            'id',
            'status',
            'created_at',
        )[:5]
    )
    return {
        'total': total,
        'by_status': status_counts,
        'recent': recent,
    }


def process_queued_instagram_publications() -> dict[str, int]:
    """
    Обрабатывает очередь queued-публикаций (для management-команды / cron).

    :returns: счётчики processed, published, failed, stuck_reset.
    """
    stats = {
        'processed': 0,
        'published': 0,
        'failed': 0,
        'stuck_reset': 0,
    }

    stuck_publications = InstagramPublication.objects.filter(
        status=InstagramPublication.STATUS_PUBLISHING,
    )
    for publication in stuck_publications:
        before_status = publication.status
        mark_stuck_instagram_publication_failed(publication)
        publication.refresh_from_db()
        if publication.status == InstagramPublication.STATUS_FAILED and before_status != publication.status:
            stats['stuck_reset'] += 1

    queued_publications = list(
        InstagramPublication.objects.filter(
            status=InstagramPublication.STATUS_QUEUED,
        ).order_by('created_at')
    )

    for publication in queued_publications:
        stats['processed'] += 1
        before_status = publication.status
        publish_instagram_publication(
            publication,
            validate_image_url=True,
            source='management',
        )
        publication.refresh_from_db()
        if publication.status == InstagramPublication.STATUS_PUBLISHED:
            stats['published'] += 1
        elif (
            publication.status == InstagramPublication.STATUS_FAILED
            and before_status != publication.status
        ):
            stats['failed'] += 1

    return stats


def is_publication_stuck_publishing(publication: InstagramPublication) -> bool:
    if publication.status != InstagramPublication.STATUS_PUBLISHING:
        return False
    started_at = publication.publishing_started_at
    if started_at is None:
        return False
    return timezone.now() - started_at >= STUCK_PUBLISHING_TIMEOUT


def mark_stuck_instagram_publication_failed(
    publication: InstagramPublication,
    *,
    message: str = 'Публикация зависла в статусе «Публикуется» более 5 минут.',
) -> InstagramPublication:
    if publication.status != InstagramPublication.STATUS_PUBLISHING:
        return publication
    if not is_publication_stuck_publishing(publication):
        return publication
    publication.status = InstagramPublication.STATUS_FAILED
    publication.error_message = message
    publication.publishing_started_at = None
    publication.save(update_fields=['status', 'error_message', 'publishing_started_at'])
    logger.warning(
        'Instagram publication #%s marked failed after stuck publishing',
        publication.pk,
    )
    return publication


def _mark_publication_failed(
    publication: InstagramPublication,
    message: str,
) -> InstagramPublication:
    publication.status = InstagramPublication.STATUS_FAILED
    publication.error_message = message
    publication.publishing_started_at = None
    publication.save(update_fields=['status', 'error_message', 'publishing_started_at'])
    return publication


def publish_instagram_publication(
    publication: InstagramPublication,
    *,
    validate_image_url: bool = False,
    source: str = 'direct',
) -> InstagramPublication:
    if publication.status == InstagramPublication.STATUS_PUBLISHED:
        logger.info(
            'Instagram publication #%s already published, skip',
            publication.pk,
        )
        return publication

    if publication.status == InstagramPublication.STATUS_CANCELLED:
        logger.info(
            'Instagram publication #%s cancelled, skip publish',
            publication.pk,
        )
        return publication

    if publication.status == InstagramPublication.STATUS_PUBLISHING:
        if is_publication_stuck_publishing(publication):
            logger.warning(
                'Instagram publication #%s stuck in publishing, waiting for reset',
                publication.pk,
            )
        else:
            logger.info(
                'Instagram publication #%s already publishing, skip',
                publication.pk,
            )
        return publication

    if publication.status not in (
        InstagramPublication.STATUS_QUEUED,
        InstagramPublication.STATUS_FAILED,
        InstagramPublication.STATUS_DRAFT,
    ):
        logger.info(
            'Instagram publication #%s status=%s, skip publish',
            publication.pk,
            publication.status,
        )
        return publication

    if not publication.image:
        return _mark_publication_failed(
            publication,
            'Не загружено изображение карточки.',
        )

    if source == 'management':
        logger.info(
            'Instagram publication #%s: started by management command',
            publication.pk,
        )
    else:
        logger.info('Instagram publication #%s: начало публикации', publication.pk)

    publication.status = InstagramPublication.STATUS_PUBLISHING
    publication.error_message = ''
    publication.publishing_started_at = timezone.now()
    publication.save(update_fields=['status', 'error_message', 'publishing_started_at'])

    try:
        result = publish_story_to_instagram(
            publication.image.name,
            publication_id=publication.pk,
            validate_image_url=validate_image_url,
        )
    except InstagramPublishError as exc:
        _mark_publication_failed(publication, str(exc))
        logger.warning(
            'Instagram publish failed for publication #%s: %s',
            publication.pk,
            exc,
        )
        return publication
    except requests.RequestException as exc:
        _mark_publication_failed(
            publication,
            f'Сетевая ошибка Meta API: {exc}',
        )
        logger.exception(
            'Instagram publish network error for publication #%s',
            publication.pk,
        )
        return publication
    except Exception as exc:
        _mark_publication_failed(publication, str(exc))
        logger.exception(
            'Unexpected Instagram publish error for publication #%s',
            publication.pk,
        )
        return publication

    publication.instagram_container_id = result.get('container_id', '')
    publication.instagram_media_id = result.get('media_id', '')
    publication.status = InstagramPublication.STATUS_PUBLISHED
    publication.published_at = timezone.now()
    publication.error_message = ''
    publication.publishing_started_at = None
    publication.save(
        update_fields=[
            'instagram_container_id',
            'instagram_media_id',
            'status',
            'published_at',
            'error_message',
            'publishing_started_at',
        ]
    )
    logger.info(
        'Instagram publication #%s published (media_id=%s)',
        publication.pk,
        publication.instagram_media_id,
    )
    return publication


def approve_instagram_publication(publication: InstagramPublication) -> InstagramPublication:
    if publication.status in (
        InstagramPublication.STATUS_DRAFT,
        InstagramPublication.STATUS_FAILED,
    ):
        publication.status = InstagramPublication.STATUS_APPROVED
        publication.error_message = ''
        publication.publishing_started_at = None
        publication.save(update_fields=['status', 'error_message', 'publishing_started_at'])
    return publication


def cancel_instagram_publication(publication: InstagramPublication) -> InstagramPublication:
    if publication.status == InstagramPublication.STATUS_PUBLISHED:
        return publication
    publication.status = InstagramPublication.STATUS_CANCELLED
    publication.save(update_fields=['status'])
    return publication
