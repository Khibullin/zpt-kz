"""Brave Image Search for seller product photos. Never auto-selects an image."""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import parse, request as urllib_request

from django.conf import settings

from catalog.article_utils import normalize_article
from catalog.remote_image import sign_remote_image_token

logger = logging.getLogger(__name__)

BRAVE_IMAGES_API_URL = 'https://api.search.brave.com/res/v1/images/search'
DEFAULT_SEARCH_TIMEOUT = 10.0
SEARCH_COUNT = 10
MAX_RESULTS = 6
PHOTO_WARNING = 'Проверьте, что на фото изображён именно ваш товар.'


class ProductImageSearchError(Exception):
    """Brave image search failure."""


@dataclass(frozen=True)
class ImageCandidate:
    thumbnail_url: str
    image_url: str
    source_url: str
    source: str
    title: str
    description: str
    confidence: str
    score: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            'token': sign_remote_image_token(
                image_url=self.image_url,
                thumbnail_url=self.thumbnail_url,
                source_url=self.source_url,
                title=self.title,
            ),
            'thumbnail_url': self.thumbnail_url,
            'source_url': self.source_url,
            'source': self.source,
            'title': self.title,
            'confidence': self.confidence,
        }


def build_image_search_query(
    article: str,
    *,
    title: str = '',
    brand: str = '',
) -> str:
    parts = [str(article or '').strip()]
    brand_text = str(brand or '').strip()
    title_text = str(title or '').strip()
    if brand_text:
        parts.append(brand_text)
    if title_text and title_text.casefold() != brand_text.casefold():
        parts.append(title_text)
    return ' '.join(part for part in parts if part)


def _text_blob(item: dict[str, Any]) -> str:
    properties = item.get('properties') if isinstance(item.get('properties'), dict) else {}
    bits = [
        item.get('title'),
        item.get('source'),
        item.get('url'),
        item.get('description'),
        properties.get('url'),
    ]
    return ' '.join(str(bit or '') for bit in bits)


def _confidence_label(score: int) -> str:
    if score >= 100:
        return 'high'
    if score >= 40:
        return 'medium'
    return 'low'


def relevance_score(item: dict[str, Any], article: str) -> int:
    raw_article = str(article or '').strip()
    key = normalize_article(raw_article)
    blob = _text_blob(item)
    compact = normalize_article(blob)
    score = 0
    if key and key in compact:
        score += 100
    if raw_article and raw_article.casefold() in blob.casefold():
        score += 50
    return score


def parse_brave_image_results(payload: Any, article: str) -> list[ImageCandidate]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get('results') or payload.get('images', {}).get('results') or []
    if not isinstance(rows, list):
        return []

    seen: set[str] = set()
    parsed: list[ImageCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        properties = row.get('properties') if isinstance(row.get('properties'), dict) else {}
        thumbnail = row.get('thumbnail') if isinstance(row.get('thumbnail'), dict) else {}
        image_url = str(properties.get('url') or '').strip()
        thumbnail_url = str(thumbnail.get('src') or '').strip()
        source_url = str(row.get('url') or '').strip()
        if not image_url or not thumbnail_url:
            continue
        if image_url in seen:
            continue
        seen.add(image_url)
        score = relevance_score(row, article)
        parsed.append(
            ImageCandidate(
                thumbnail_url=thumbnail_url,
                image_url=image_url,
                source_url=source_url,
                source=str(row.get('source') or '').strip(),
                title=str(row.get('title') or '').strip(),
                description=str(row.get('description') or '').strip(),
                confidence=_confidence_label(score),
                score=score,
            )
        )

    parsed.sort(key=lambda item: (-item.score, item.title))
    return parsed[:MAX_RESULTS]


def _decode_body(raw_body: bytes, content_encoding: str) -> bytes:
    encoding = (content_encoding or '').strip().lower()
    is_gzip = encoding == 'gzip' or (len(raw_body) >= 2 and raw_body[:2] == b'\x1f\x8b')
    if not is_gzip:
        return raw_body
    try:
        return gzip.decompress(raw_body)
    except OSError:
        return raw_body


def _urlopen_without_proxy(http_request: urllib_request.Request, timeout: float):
    opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
    return opener.open(http_request, timeout=timeout)


def fetch_brave_image_payload(
    query: str,
    *,
    api_key: str,
    count: int = SEARCH_COUNT,
    timeout: float = DEFAULT_SEARCH_TIMEOUT,
    urlopen: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    key = (api_key or '').strip()
    if not key:
        raise ProductImageSearchError('BRAVE_SEARCH_API_KEY не задан.')

    params = {
        'q': query,
        'count': max(1, min(int(count), 20)),
        'country': 'ALL',
        'safesearch': 'strict',
    }
    request_url = f'{BRAVE_IMAGES_API_URL}?{parse.urlencode(params, encoding="utf-8")}'
    http_request = urllib_request.Request(
        request_url,
        headers={
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'X-Subscription-Token': key,
        },
        method='GET',
    )
    open_url = urlopen or _urlopen_without_proxy
    try:
        with open_url(http_request, timeout=timeout) as response:
            raw_body = response.read()
            headers = response.headers
            status_code = int(getattr(response, 'status', 200) or 200)
    except urllib_error.HTTPError as exc:
        raise ProductImageSearchError(f'Brave Image Search HTTP {exc.code}') from exc
    except urllib_error.URLError as exc:
        raise ProductImageSearchError(f'Brave Image Search network error: {exc.reason}') from exc

    if status_code >= 400:
        raise ProductImageSearchError(f'Brave Image Search HTTP {status_code}')

    encoding = ''
    if headers is not None and hasattr(headers, 'get'):
        encoding = str(headers.get('Content-Encoding') or '')
    decoded = _decode_body(raw_body, encoding)
    try:
        payload = json.loads(decoded.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductImageSearchError('Brave Image Search returned invalid JSON') from exc
    if not isinstance(payload, dict):
        raise ProductImageSearchError('Brave Image Search returned invalid JSON')
    return payload


def search_product_images(
    article: str,
    *,
    title: str = '',
    brand: str = '',
    urlopen: Callable[..., Any] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    raw_article = str(article or '').strip()
    if not raw_article:
        return {
            'ok': False,
            'error': 'Укажите артикул, чтобы найти фото.',
            'warning': PHOTO_WARNING,
            'query': '',
            'images': [],
        }

    key = (api_key if api_key is not None else getattr(settings, 'BRAVE_SEARCH_API_KEY', '')) or ''
    key = str(key).strip()
    query = build_image_search_query(raw_article, title=title, brand=brand)
    if not key:
        return {
            'ok': False,
            'error': 'Поиск фото сейчас недоступен. Загрузите своё фото.',
            'warning': PHOTO_WARNING,
            'query': query,
            'images': [],
        }

    try:
        payload = fetch_brave_image_payload(query, api_key=key, urlopen=urlopen)
        candidates = parse_brave_image_results(payload, raw_article)
    except ProductImageSearchError as exc:
        logger.warning('Product image search failed: %s', exc)
        return {
            'ok': False,
            'error': 'Не удалось найти фото. Загрузите своё фото.',
            'warning': PHOTO_WARNING,
            'query': query,
            'images': [],
        }

    return {
        'ok': True,
        'error': '',
        'warning': PHOTO_WARNING,
        'query': query,
        'images': [item.to_public_dict() for item in candidates],
    }
