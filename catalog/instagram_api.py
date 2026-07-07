"""
Публикация сгенерированных карточек в Instagram через Meta Graph API.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_BASE = 'https://graph.facebook.com'
CONTAINER_POLL_INTERVAL_SEC = 2
CONTAINER_POLL_TIMEOUT_SEC = 60
REQUEST_TIMEOUT_SEC = 30
IMAGE_URL_USER_AGENT = 'ZPT.KZ-Instagram-Validator/1.0'
BLOCKED_URL_PATH_MARKERS = (
    '/admin/',
    '/login/',
    '/accounts/login/',
    '/auth/',
)
SENSITIVE_LOG_KEYS = ('access_token', 'token', 'client_secret', 'appsecret_proof')


class InstagramPublishError(Exception):
    """Ошибка публикации Instagram Story через Meta Graph API."""


@dataclass(frozen=True)
class ImageUrlValidationResult:
    status_code: int
    content_type: str
    final_url: str


def _pub_log(
    publication_id: int | None,
    level: int,
    message: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    prefix = (
        f'Instagram publication #{publication_id}'
        if publication_id is not None
        else 'Instagram publish'
    )
    logger.log(level, '%s: ' + message, prefix, *args, **kwargs)


def _sanitize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_LOG_KEYS:
                sanitized[str(key)] = '***'
            else:
                sanitized[str(key)] = _sanitize_for_log(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_log(item) for item in value]
    if isinstance(value, str) and 'access_token=' in value:
        return _redact_url(value)
    return value


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    safe_query = [
        (key, '***' if key.lower() in SENSITIVE_LOG_KEYS else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(parsed._replace(query=urlencode(safe_query)))


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
    """Собирает абсолютный публичный HTTPS URL файла из MEDIA_ROOT."""
    clean_path = normalize_media_relative_path(image_relative_path)
    base_url = getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz').strip().rstrip('/')
    if base_url.startswith('http://'):
        base_url = f'https://{base_url[len("http://"):]}'
    elif not base_url.startswith('https://'):
        base_url = f'https://{base_url.lstrip("/")}'

    media_url = getattr(settings, 'MEDIA_URL', '/products/').strip('/')
    return f'{base_url}/{media_url}/{clean_path}'


def normalize_media_relative_path(image_path: str) -> str:
    """
    Приводит путь к виду ``instagram_stories/file.jpg`` относительно MEDIA_ROOT.

    :raises InstagramPublishError: если передан локальный путь вне MEDIA_ROOT,
        URL admin/login или другой недопустимый путь.
    """
    raw = str(image_path or '').strip().replace('\\', '/')
    if not raw:
        raise InstagramPublishError('Путь к изображению Story не указан.')

    lowered = raw.lower()
    if raw.startswith(('http://', 'https://')):
        raise InstagramPublishError(
            'В Instagram API нужно передавать путь в MEDIA_ROOT, а не готовый URL.'
        )
    if any(marker in lowered for marker in BLOCKED_URL_PATH_MARKERS):
        raise InstagramPublishError(
            'Путь к изображению указывает на защищённую страницу, а не на media-файл.'
        )
    if re.match(r'^[a-zA-Z]:', raw) or raw.startswith('//'):
        raw = absolute_media_path_to_relative(raw)

    clean_path = raw.lstrip('/')
    if '..' in PurePosixPath(clean_path).parts:
        raise InstagramPublishError('Недопустимый путь к изображению Story.')

    return clean_path


def validate_public_image_url(image_url: str) -> ImageUrlValidationResult:
    """
    Проверяет, что Meta сможет скачать JPEG по публичному URL без авторизации.

    :returns: HTTP status и Content-Type успешной проверки.
    :raises InstagramPublishError: если URL недоступен или ответ не является JPEG.
    """
    parsed = urlparse(image_url)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise InstagramPublishError(
            f'image_url должен быть абсолютным HTTPS-адресом, получено: {image_url}'
        )
    if _url_looks_like_blocked_destination(image_url):
        raise InstagramPublishError(
            f'image_url ведёт на защищённую страницу, а не на media-файл: {image_url}'
        )

    try:
        response = requests.get(
            image_url,
            timeout=REQUEST_TIMEOUT_SEC,
            allow_redirects=True,
            headers={'User-Agent': IMAGE_URL_USER_AGENT},
        )
    except requests.RequestException as exc:
        raise InstagramPublishError(
            f'Не удалось проверить доступность image_url {image_url}: {exc}'
        ) from exc

    content_type = (response.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()

    if _url_looks_like_blocked_destination(response.url):
        raise InstagramPublishError(
            f'image_url перенаправлен на защищённую страницу: {response.url}'
        )
    if response.status_code != 200:
        raise InstagramPublishError(
            f'image_url недоступен для Meta API: HTTP {response.status_code} ({image_url})'
        )

    if not content_type.startswith('image/jpeg'):
        raise InstagramPublishError(
            f'image_url должен отдавать image/jpeg, получено {content_type or "unknown"} '
            f'({image_url})'
        )

    content_length_header = response.headers.get('Content-Length')
    if content_length_header is not None:
        try:
            content_length = int(content_length_header)
        except ValueError as exc:
            raise InstagramPublishError(
                f'image_url вернул некорректный Content-Length ({image_url})'
            ) from exc
        if content_length <= 0:
            raise InstagramPublishError(
                f'image_url вернул пустой JPEG (Content-Length={content_length}).'
            )
    elif not response.content:
        raise InstagramPublishError(
            f'image_url вернул пустой ответ без JPEG-данных ({image_url}).'
        )

    if _response_looks_like_html(response):
        raise InstagramPublishError(
            f'image_url вернул HTML вместо JPEG, вероятно требуется авторизация ({image_url}).'
        )

    return ImageUrlValidationResult(
        status_code=response.status_code,
        content_type=content_type,
        final_url=response.url,
    )


def _url_looks_like_blocked_destination(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(marker in path for marker in BLOCKED_URL_PATH_MARKERS)


def _response_looks_like_html(response: requests.Response) -> bool:
    content_type = (response.headers.get('Content-Type') or '').lower()
    if 'text/html' in content_type:
        return True
    sample = response.content[:512].lstrip().lower()
    return sample.startswith(b'<!doctype html') or sample.startswith(b'<html')


def absolute_media_path_to_relative(path: Path | str) -> str:
    """Преобразует абсолютный путь в MEDIA_ROOT в относительный URL-путь."""
    file_path = Path(path).resolve()
    media_root = Path(settings.MEDIA_ROOT).resolve()
    relative = file_path.relative_to(media_root)
    return str(relative).replace('\\', '/')


def _parse_graph_response(
    response: requests.Response,
    *,
    action: str,
    publication_id: int | None = None,
) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        _pub_log(
            publication_id,
            logging.ERROR,
            '%s: HTTP %s non-JSON response=%s',
            action,
            response.status_code,
            response.text[:500],
        )
        raise InstagramPublishError(
            f'{action}: Meta API вернул не-JSON ответ (HTTP {response.status_code}).'
        ) from exc

    _pub_log(
        publication_id,
        logging.INFO,
        '%s: HTTP %s response=%s',
        action,
        response.status_code,
        _sanitize_for_log(payload),
    )

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
    publication_id: int | None = None,
) -> str:
    endpoint = _redact_url(f'{_graph_api_root()}/{ig_account_id}/media')
    _pub_log(
        publication_id,
        logging.INFO,
        'создаём media container endpoint=%s media_type=STORIES',
        endpoint,
    )
    response = requests.post(
        f'{_graph_api_root()}/{ig_account_id}/media',
        data={
            'image_url': image_url,
            'media_type': 'STORIES',
            'access_token': access_token,
        },
        timeout=REQUEST_TIMEOUT_SEC,
    )
    payload = _parse_graph_response(
        response,
        action='Создание media container',
        publication_id=publication_id,
    )
    container_id = payload.get('id')
    if not container_id:
        raise InstagramPublishError(
            'Создание media container: Meta API не вернул ID контейнера.'
        )
    container_id = str(container_id)
    _pub_log(publication_id, logging.INFO, 'получен container_id=%s', container_id)
    return container_id


def _wait_for_container_ready(
    *,
    container_id: str,
    access_token: str,
    publication_id: int | None = None,
) -> None:
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
        payload = _parse_graph_response(
            response,
            action='Polling media container',
            publication_id=publication_id,
        )
        status_code = (payload.get('status_code') or '').upper()
        _pub_log(
            publication_id,
            logging.INFO,
            'polling container_id=%s status_code=%s',
            container_id,
            status_code or 'EMPTY',
        )

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
    publication_id: int | None = None,
) -> str:
    endpoint = _redact_url(f'{_graph_api_root()}/{ig_account_id}/media_publish')
    _pub_log(
        publication_id,
        logging.INFO,
        'вызываем media_publish endpoint=%s container_id=%s',
        endpoint,
        container_id,
    )
    response = requests.post(
        f'{_graph_api_root()}/{ig_account_id}/media_publish',
        data={
            'creation_id': container_id,
            'access_token': access_token,
        },
        timeout=REQUEST_TIMEOUT_SEC,
    )
    payload = _parse_graph_response(
        response,
        action='Публикация media container',
        publication_id=publication_id,
    )
    media_id = payload.get('id')
    if not media_id:
        raise InstagramPublishError(
            'Публикация media container: Meta API не вернул ID опубликованного media.'
        )
    media_id = str(media_id)
    _pub_log(publication_id, logging.INFO, 'получен media_id=%s', media_id)
    return media_id


def publish_story_to_instagram(
    image_relative_path: str,
    *,
    publication_id: int | None = None,
) -> dict[str, str]:
    """
    Публикует изображение в Instagram Stories через Meta Graph API.

    :param image_relative_path: путь относительно MEDIA_ROOT, например
        ``instagram_stories/request_<uuid>_20260706_150000.jpg``.
    :returns: dict с ключами ``container_id`` и ``media_id``.
    :raises InstagramPublishError: если публикация не удалась.
    """
    try:
        if not instagram_credentials_configured():
            raise InstagramPublishError(
                'Instagram API не настроен: отсутствуют INSTAGRAM_ACCOUNT_ID '
                'или INSTAGRAM_ACCESS_TOKEN.'
            )

        ig_account_id = _instagram_account_id()
        access_token = _instagram_access_token()
        image_url = build_public_media_url(image_relative_path)

        _pub_log(publication_id, logging.INFO, 'начало публикации')
        _pub_log(publication_id, logging.INFO, 'image_url=%s', image_url)

        validation = validate_public_image_url(image_url)
        _pub_log(
            publication_id,
            logging.INFO,
            'проверка image_url: HTTP %s Content-Type=%s',
            validation.status_code,
            validation.content_type,
        )

        container_id = _create_story_container(
            ig_account_id=ig_account_id,
            access_token=access_token,
            image_url=image_url,
            publication_id=publication_id,
        )

        _wait_for_container_ready(
            container_id=container_id,
            access_token=access_token,
            publication_id=publication_id,
        )

        media_id = _publish_story_container(
            ig_account_id=ig_account_id,
            access_token=access_token,
            container_id=container_id,
            publication_id=publication_id,
        )
        return {
            'container_id': container_id,
            'media_id': media_id,
        }
    except InstagramPublishError:
        _pub_log(publication_id, logging.ERROR, 'ошибка публикации', exc_info=True)
        raise
    except requests.RequestException:
        _pub_log(publication_id, logging.ERROR, 'сетевая ошибка Meta API', exc_info=True)
        raise
    except Exception:
        _pub_log(publication_id, logging.ERROR, 'непредвиденная ошибка публикации', exc_info=True)
        raise


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
