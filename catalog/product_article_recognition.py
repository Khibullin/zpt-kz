"""Extract a likely part article from a seller-uploaded product photo.

This is intentionally a narrow vision step. It never creates or updates Product rows.
The recognized article is fed into the existing product-by-article assistant by the UI.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from django.conf import settings
from django.core.exceptions import ValidationError

from catalog.image_upload_policy import optimize_uploaded_image

logger = logging.getLogger(__name__)

OPENAI_RESPONSES_URL = 'https://api.openai.com/v1/responses'
DEFAULT_PRODUCT_AI_MODEL = 'gpt-5.6-luna'
OPENAI_VISION_TIMEOUT = 30
MAX_CANDIDATES = 5
_ARTICLE_SAFE_RE = re.compile(r'[^A-Za-z0-9._/\-]+')


@dataclass(frozen=True)
class ArticleRecognition:
    article: str = ''
    candidates: list[str] = field(default_factory=list)
    confidence: str = 'needs_verification'


def _clean_article(value: object) -> str:
    text = str(value or '').strip()
    text = _ARTICLE_SAFE_RE.sub('', text)[:100]
    if sum(ch.isalnum() for ch in text) < 4:
        return ''
    return text


def _extract_output_text(payload: dict[str, Any]) -> str:
    if payload.get('output_text'):
        return str(payload.get('output_text') or '')
    chunks: list[str] = []
    for item in payload.get('output') or []:
        if not isinstance(item, dict):
            continue
        for content in item.get('content') or []:
            if not isinstance(content, dict):
                continue
            piece = content.get('text') or content.get('output_text')
            if piece:
                chunks.append(str(piece))
    return '\n'.join(chunks)


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or '').strip()
    start = raw.find('{')
    end = raw.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('no json object')
    payload = json.loads(raw[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError('json is not an object')
    return payload


def _parse_recognition(data: dict[str, Any]) -> ArticleRecognition:
    article = _clean_article(data.get('article'))
    raw_candidates = data.get('candidates') or []
    if isinstance(raw_candidates, str):
        raw_candidates = [raw_candidates]
    if not isinstance(raw_candidates, list):
        raw_candidates = []

    candidates: list[str] = []
    seen: set[str] = set()
    for raw in raw_candidates:
        candidate = _clean_article(raw)
        key = candidate.upper()
        if not candidate or key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
        if len(candidates) >= MAX_CANDIDATES:
            break

    if article and article.upper() not in seen:
        candidates.insert(0, article)
        candidates = candidates[:MAX_CANDIDATES]

    confidence = str(data.get('confidence') or 'needs_verification').strip()
    if confidence not in {'confirmed', 'likely', 'needs_verification'}:
        confidence = 'needs_verification'

    # Never auto-select when the model itself reports several plausible numbers.
    distinct = {item.upper() for item in candidates}
    if len(distinct) > 1:
        article = ''
        confidence = 'needs_verification'

    return ArticleRecognition(
        article=article,
        candidates=candidates,
        confidence=confidence,
    )


def recognize_article_from_upload(
    uploaded,
    *,
    urlopen: Callable[..., Any] | None = None,
) -> ArticleRecognition | None:
    """Return a narrow article recognition result or None if AI is unavailable."""
    if not uploaded:
        raise ValidationError('Выберите фотографию с артикулом.')

    optimized = optimize_uploaded_image(uploaded)
    optimized.seek(0)
    image_bytes = optimized.read()
    if not image_bytes:
        raise ValidationError('Не удалось прочитать фотографию.')

    api_key = (getattr(settings, 'OPENAI_API_KEY', '') or '').strip()
    if not api_key:
        return None

    model = (
        getattr(settings, 'PRODUCT_AI_MODEL', '') or ''
    ).strip() or DEFAULT_PRODUCT_AI_MODEL
    data_url = (
        'data:image/webp;base64,'
        + base64.b64encode(image_bytes).decode('ascii')
    )
    prompt = (
        'Определи артикул автозапчасти по фотографии упаковки, этикетки или самой детали. '
        'Артикул — это OEM/part number/SKU детали, а не штрихкод, цена, дата, телефон, '
        'сертификат, модель автомобиля или название бренда. '
        'Не угадывай символы, которых не видно. '
        'Если виден один однозначный артикул, верни его в article. '
        'Если есть несколько реально возможных артикулов, article оставь пустым и перечисли '
        'до 5 вариантов в candidates. Если артикула не видно, оба поля оставь пустыми. '
        'Сохраняй дефисы, точки и слэши, если они напечатаны как часть номера. '
        'Верни ТОЛЬКО JSON без markdown: '
        '{"article":"","candidates":[],"confidence":"confirmed|likely|needs_verification"}'
    )
    payload = {
        'model': model,
        'input': [
            {
                'role': 'user',
                'content': [
                    {'type': 'input_text', 'text': prompt},
                    {
                        'type': 'input_image',
                        'image_url': data_url,
                        'detail': 'high',
                    },
                ],
            },
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib_request.Request(
        OPENAI_RESPONSES_URL,
        data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
    )
    open_url = urlopen or (
        lambda req, timeout: urllib_request.build_opener(
            urllib_request.ProxyHandler({})
        ).open(req, timeout=timeout)
    )

    try:
        with open_url(request, timeout=OPENAI_VISION_TIMEOUT) as response:
            raw = response.read()
            status = int(getattr(response, 'status', 200) or 200)
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        logger.warning('OpenAI article recognition unavailable: %s', exc)
        return None

    if status >= 400:
        logger.warning('OpenAI article recognition HTTP %s', status)
        return None

    try:
        response_payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning('OpenAI article recognition returned invalid JSON')
        return None
    if not isinstance(response_payload, dict):
        return None

    try:
        data = _extract_json_object(_extract_output_text(response_payload))
    except (ValueError, json.JSONDecodeError):
        logger.warning('OpenAI article recognition JSON payload missing')
        return None

    return _parse_recognition(data)
