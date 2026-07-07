"""
Публикация сгенерированных карточек в Instagram через Meta Graph API.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_BASE = 'https://graph.facebook.com'
CONTAINER_POLL_INTERVAL_SEC = 2
CONTAINER_POLL_TIMEOUT_SEC = 60
REQUEST_TIMEOUT_SEC = 30


class InstagramPublishError(Exception):
    """Ошибка публикации Instagram Story через Meta Graph API."""


def _graph_api_version() -> str:
    return getattr(settings, 'META_GRAPH_API_VERSION', 'v20.0').strip() or 'v20.0'


def _graph_api_root() -> str:
    return f'{GRAPH_API_BASE}/{_graph_api_version()}'


def instagram_credentials_configured() -> bool:
    account_id = (
        getattr(settings, 'INSTAGRAM_ACCOUNT_ID', '')
        or getattr(settings, 'INSTAGRAM_BUSINESS_ACCOUNT_ID', '')
        or ''
    )
    access_token = (
        getattr(settings, 'INSTAGRAM_ACCESS_TOKEN', '')
        or getattr(settings, 'FACEBOOK_ACCESS_TOKEN', '')
        or ''
    )
    return bool(str(account_id).strip() and str(access_token).strip())


def _instagram_account_id() -> str:
    return str(
        getattr(settings, 'INSTAGRAM_ACCOUNT_ID', '')
        or getattr(settings, 'INSTAGRAM_BUSINESS_ACCOUNT_ID', '')
        or ''
    ).strip()


def _instagram_access_token() -> str:
    return str(
        getattr(settings, 'INSTAGRAM_ACCESS_TOKEN', '')
        or getattr(settings, 'FACEBOOK_ACCESS_TOKEN', '')
        or ''
    ).strip()


def build_public_media_url(image_relative_path: str) -> str:
    """Собирает абсолютный публичный URL файла из MEDIA_ROOT."""
    base_url = getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')
    media_url = getattr(settings, 'MEDIA_URL', '/products/').strip('/')
    clean_path = image_relative_path.replace('\\', '/').lstrip('/')
    return f'{base_url}/{media_url}/{clean_path}'


def absolute_media_path_to_relative(path: Path | str) -> str:
    """Преобразует абсолютный путь в MEDIA_ROOT в относительный URL-путь."""
    file_path = Path(path).resolve()
    media_root = Path(settings.MEDIA_ROOT).resolve()
    relative = file_path.relative_to(media_root)
    return str(relative).replace('\\', '/')


def _parse_graph_response(response: requests.Response, *, action: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise InstagramPublishError(
            f'{action}: Meta API вернул не-JSON ответ (HTTP {response.status_code}).'
        ) from exc

    if response.status_code >= 400 or 'error' in payload:
        error = payload.get('error', {})
        message = error.get('message') or response.text or 'Неизвестная ошибка Meta API'
        code = error.get('code')
        raise InstagramPublishError(f'{action}: {message} (code={code})')

    return payload


def _create_story_container(
    *,
    ig_account_id: str,
    access_token: str,
    image_url: str,
) -> str:
    endpoint = f'{_graph_api_root()}/{ig_account_id}/media'
    response = requests.post(
        endpoint,
        data={
            'image_url': image_url,
            'media_type': 'STORIES',
            'access_token': access_token,
        },
        timeout=REQUEST_TIMEOUT_SEC,
    )
    payload = _parse_graph_response(response, action='Создание media container')
    container_id = payload.get('id')
    if not container_id:
        raise InstagramPublishError(
            'Создание media container: Meta API не вернул ID контейнера.'
        )
    return str(container_id)


def _wait_for_container_ready(*, container_id: str, access_token: str) -> None:
    endpoint = f'{_graph_api_root()}/{container_id}'
    deadline = time.monotonic() + CONTAINER_POLL_TIMEOUT_SEC

    while time.monotonic() < deadline:
        response = requests.get(
            endpoint,
            params={
                'fields': 'status_code',
                'access_token': access_token,
            },
            timeout=REQUEST_TIMEOUT_SEC,
        )
        payload = _parse_graph_response(response, action='Проверка статуса контейнера')
        status_code = (payload.get('status_code') or '').upper()

        if status_code in ('', 'FINISHED'):
            return
        if status_code == 'ERROR':
            raise InstagramPublishError(
                f'Контейнер {container_id}: Meta вернула status_code=ERROR.'
            )
        if status_code == 'EXPIRED':
            raise InstagramPublishError(
                f'Контейнер {container_id}: Meta вернула status_code=EXPIRED.'
            )

        time.sleep(CONTAINER_POLL_INTERVAL_SEC)

    raise InstagramPublishError(
        f'Контейнер {container_id}: превышено время ожидания готовности '
        f'({CONTAINER_POLL_TIMEOUT_SEC} сек).'
    )


def _publish_story_container(
    *,
    ig_account_id: str,
    access_token: str,
    container_id: str,
) -> str:
    endpoint = f'{_graph_api_root()}/{ig_account_id}/media_publish'
    response = requests.post(
        endpoint,
        data={
            'creation_id': container_id,
            'access_token': access_token,
        },
        timeout=REQUEST_TIMEOUT_SEC,
    )
    payload = _parse_graph_response(response, action='Публикация media container')
    media_id = payload.get('id')
    if not media_id:
        raise InstagramPublishError(
            'Публикация media container: Meta API не вернул ID опубликованного media.'
        )
    return str(media_id)


def publish_story_to_instagram(image_relative_path: str) -> dict[str, str]:
    """
    Публикует изображение в Instagram Stories через Meta Graph API.

    :param image_relative_path: путь относительно MEDIA_ROOT, например
        ``instagram_stories/request_12_20260706_150000.png``.
    :returns: dict с ключами ``container_id`` и ``media_id``.
    :raises InstagramPublishError: если публикация не удалась.
    """
    if not instagram_credentials_configured():
        raise InstagramPublishError(
            'Instagram API не настроен: отсутствуют INSTAGRAM_ACCOUNT_ID '
            'или INSTAGRAM_ACCESS_TOKEN.'
        )

    ig_account_id = _instagram_account_id()
    access_token = _instagram_access_token()
    image_url = build_public_media_url(image_relative_path)

    logger.info(
        'Instagram publish: создаём Story container (account=%s, image=%s)',
        ig_account_id,
        image_url,
    )

    container_id = _create_story_container(
        ig_account_id=ig_account_id,
        access_token=access_token,
        image_url=image_url,
    )
    logger.info('Instagram publish: container создан (id=%s)', container_id)

    _wait_for_container_ready(
        container_id=container_id,
        access_token=access_token,
    )
    logger.info('Instagram publish: container готов (id=%s)', container_id)

    media_id = _publish_story_container(
        ig_account_id=ig_account_id,
        access_token=access_token,
        container_id=container_id,
    )
    logger.info('Instagram publish: Story опубликована (media_id=%s)', media_id)
    return {
        'container_id': container_id,
        'media_id': media_id,
    }


def try_publish_story_to_instagram(image_relative_path: str) -> str | None:
    """
    Безопасно публикует Story в Instagram.

    Если токены не настроены или Meta API недоступен — логирует и возвращает None.
    """
    if not instagram_credentials_configured():
        logger.info(
            'Instagram publish пропущен: не заданы INSTAGRAM_BUSINESS_ACCOUNT_ID '
            'и/или FACEBOOK_ACCESS_TOKEN.'
        )
        return None

    try:
        result = publish_story_to_instagram(image_relative_path)
        return result.get('media_id')
    except InstagramPublishError as exc:
        logger.warning('Instagram publish не выполнен: %s', exc)
        return None
    except requests.RequestException as exc:
        logger.warning('Instagram publish: сетевая ошибка Meta API: %s', exc)
        return None
    except Exception:
        logger.exception('Instagram publish: непредвиденная ошибка')
        return None
