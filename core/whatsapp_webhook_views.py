from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.whatsapp_inbound import (
    process_whatsapp_webhook_payload,
    signatures_match,
    verify_tokens_match,
)

logger = logging.getLogger(__name__)


def _verify_token() -> str:
    return str(getattr(settings, 'WHATSAPP_WEBHOOK_VERIFY_TOKEN', '') or '').strip()


def _app_secret() -> str:
    return str(getattr(settings, 'META_APP_SECRET', '') or '').strip()


def _handle_verification(request) -> HttpResponse:
    mode = str(request.GET.get('hub.mode') or '')
    token = str(request.GET.get('hub.verify_token') or '')
    challenge = request.GET.get('hub.challenge')
    expected = _verify_token()
    if mode != 'subscribe' or challenge is None or not verify_tokens_match(token, expected):
        return HttpResponseForbidden()
    return HttpResponse(str(challenge), content_type='text/plain; charset=utf-8')


def _handle_event(request) -> HttpResponse:
    secret = _app_secret()
    signature = request.headers.get('X-Hub-Signature-256', '')
    raw_body = request.body or b''
    if not signatures_match(raw_body, signature, secret):
        return HttpResponseForbidden()
    try:
        payload = json.loads(raw_body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning('whatsapp webhook rejected: invalid json after signature check')
        return HttpResponse(status=200)
    process_whatsapp_webhook_payload(payload, raw_body=raw_body)
    return HttpResponse(status=200)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def whatsapp_webhook(request):
    if request.method == 'GET':
        return _handle_verification(request)
    return _handle_event(request)
