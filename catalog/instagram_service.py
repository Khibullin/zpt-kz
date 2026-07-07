"""
Оркестрация безопасной публикации заявок в Instagram Stories.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import IntegrityError
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
from core.models import InstagramPublication, Request

logger = logging.getLogger(__name__)


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
            InstagramPublication.STATUS_APPROVED,
            InstagramPublication.STATUS_FAILED,
        ):
            return publish_instagram_publication(existing)
        return existing

    try:
        product_request = Request.objects.get(pk=request_id)
    except Request.DoesNotExist:
        logger.warning('Request #%s not found for Instagram publication', request_id)
        return None

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

    if mode == 'LIVE':
        return publish_instagram_publication(publication)

    return publication


def publish_instagram_publication(publication: InstagramPublication) -> InstagramPublication:
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
        logger.info(
            'Instagram publication #%s already publishing, skip',
            publication.pk,
        )
        return publication

    if not publication.image:
        publication.status = InstagramPublication.STATUS_FAILED
        publication.error_message = 'Не загружено изображение карточки.'
        publication.save(update_fields=['status', 'error_message'])
        return publication

    publication.status = InstagramPublication.STATUS_PUBLISHING
    publication.error_message = ''
    publication.save(update_fields=['status', 'error_message'])

    try:
        result = publish_story_to_instagram(publication.image.name)
    except InstagramPublishError as exc:
        publication.status = InstagramPublication.STATUS_FAILED
        publication.error_message = str(exc)
        publication.save(update_fields=['status', 'error_message'])
        logger.warning(
            'Instagram publish failed for publication #%s: %s',
            publication.pk,
            exc,
        )
        return publication
    except Exception as exc:
        publication.status = InstagramPublication.STATUS_FAILED
        publication.error_message = str(exc)
        publication.save(update_fields=['status', 'error_message'])
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
    publication.save(
        update_fields=[
            'instagram_container_id',
            'instagram_media_id',
            'status',
            'published_at',
            'error_message',
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
        publication.save(update_fields=['status', 'error_message'])
    return publication


def cancel_instagram_publication(publication: InstagramPublication) -> InstagramPublication:
    if publication.status == InstagramPublication.STATUS_PUBLISHED:
        return publication
    publication.status = InstagramPublication.STATUS_CANCELLED
    publication.save(update_fields=['status'])
    return publication
