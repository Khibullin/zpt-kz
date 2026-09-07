from __future__ import annotations

import json
import logging

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST

from core.models import (
    SELLER_CONFIRM_NO_TEXT,
    SELLER_CONFIRM_YES_TEXT,
)
from core.services.seller_identity import get_logged_request_seller
from core.services.seller_whatsapp_consent import (
    CONSENT_ACTION_GRANT,
    CONSENT_ACTION_REVOKE,
    USED_OR_STALE_LINK_MESSAGE,
    SellerWhatsAppConsentError,
    SellerWhatsAppConsentTokenExpired,
    SellerWhatsAppConsentTokenInvalid,
    SellerWhatsAppConsentTokenStale,
    apply_seller_link_whatsapp_consent,
    apply_seller_portal_whatsapp_consent,
    get_seller_whatsapp_marketing_consent_status,
    is_seller_whatsapp_consent_link_consumed,
    resolve_seller_from_whatsapp_consent_token,
)

logger = logging.getLogger(__name__)


def _parse_action(value: object) -> str:
    action = str(value or '').strip().lower()
    if action in {CONSENT_ACTION_GRANT, 'yes', '1', 'true'}:
        return CONSENT_ACTION_GRANT
    if action in {CONSENT_ACTION_REVOKE, 'no', '0', 'false'}:
        return CONSENT_ACTION_REVOKE
    return ''


def _consent_payload(seller, consent_status: str) -> dict:
    return {
        'status': 'ok',
        'consent_status': consent_status,
        'receive_requests': seller.receive_requests,
        'is_paused': seller.is_paused,
        'is_active': seller.is_active,
    }


def _wants_json(request) -> bool:
    content_type = str(request.content_type or '').lower()
    accept = str(request.headers.get('Accept') or '').lower()
    return 'application/json' in content_type or 'application/json' in accept


@require_http_methods(['GET', 'POST'])
def seller_whatsapp_consent_api(request):
    seller = get_logged_request_seller(request)
    if seller is None:
        if request.method == 'GET' or _wants_json(request):
            return JsonResponse({'error': 'Требуется вход продавца'}, status=401)
        return redirect('seller_login')

    if request.method == 'GET':
        return JsonResponse(_consent_payload(
            seller,
            get_seller_whatsapp_marketing_consent_status(seller),
        ))

    action = ''
    if 'application/json' in str(request.content_type or '').lower():
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JsonResponse({'error': 'invalid json'}, status=400)
        if not isinstance(payload, dict):
            return JsonResponse({'error': 'invalid json'}, status=400)
        action = _parse_action(payload.get('action'))
    else:
        action = _parse_action(request.POST.get('action'))

    if action not in {CONSENT_ACTION_GRANT, CONSENT_ACTION_REVOKE}:
        return JsonResponse({'error': 'Укажите действие grant или revoke'}, status=400)

    try:
        consent = apply_seller_portal_whatsapp_consent(
            seller,
            granted=action == CONSENT_ACTION_GRANT,
        )
    except SellerWhatsAppConsentError as exc:
        return JsonResponse({'error': exc.message}, status=400)

    seller.refresh_from_db(fields=['receive_requests', 'is_paused', 'is_active'])
    if _wants_json(request) or 'application/json' in str(request.content_type or '').lower():
        return JsonResponse(_consent_payload(seller, consent.status))
    return redirect('seller_dashboard')


@require_POST
def seller_portal_whatsapp_consent(request):
    seller = get_logged_request_seller(request)
    if seller is None:
        return redirect('seller_login')
    action = _parse_action(request.POST.get('action'))
    if action not in {CONSENT_ACTION_GRANT, CONSENT_ACTION_REVOKE}:
        return redirect('seller_dashboard')
    try:
        apply_seller_portal_whatsapp_consent(
            seller,
            granted=action == CONSENT_ACTION_GRANT,
        )
    except SellerWhatsAppConsentError:
        logger.warning(
            'seller portal whatsapp consent failed seller_id=%s',
            seller.pk,
        )
    return redirect('seller_dashboard')


def _link_error_response(request, message: str, status: int):
    return render(
        request,
        'core/seller_whatsapp_consent.html',
        {
            'page_state': 'error',
            'error_message': message,
            'seller_name': '',
            'yes_label': SELLER_CONFIRM_YES_TEXT,
            'no_label': SELLER_CONFIRM_NO_TEXT,
        },
        status=status,
    )


@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def seller_whatsapp_consent_link(request, token: str):
    try:
        resolved = resolve_seller_from_whatsapp_consent_token(token)
    except SellerWhatsAppConsentTokenExpired:
        return _link_error_response(
            request,
            'Ссылка подтверждения устарела.',
            410,
        )
    except SellerWhatsAppConsentTokenInvalid:
        return _link_error_response(request, 'Ссылка недействительна.', 404)

    seller = resolved.seller
    if is_seller_whatsapp_consent_link_consumed(seller, resolved.issued_at):
        return _link_error_response(request, USED_OR_STALE_LINK_MESSAGE, 409)

    result_message = ''
    page_state = 'form'
    if request.method == 'POST':
        action = _parse_action(request.POST.get('action'))
        if action not in {CONSENT_ACTION_GRANT, CONSENT_ACTION_REVOKE}:
            return render(
                request,
                'core/seller_whatsapp_consent.html',
                {
                    'page_state': 'form',
                    'seller_name': seller.name,
                    'yes_label': SELLER_CONFIRM_YES_TEXT,
                    'no_label': SELLER_CONFIRM_NO_TEXT,
                    'error_message': 'Выберите действие.',
                },
                status=400,
            )
        try:
            apply_seller_link_whatsapp_consent(
                seller,
                granted=action == CONSENT_ACTION_GRANT,
                issued_at=resolved.issued_at,
            )
        except SellerWhatsAppConsentTokenStale:
            return _link_error_response(request, USED_OR_STALE_LINK_MESSAGE, 409)
        except SellerWhatsAppConsentError:
            return _link_error_response(request, 'Ссылка недействительна.', 400)
        page_state = 'done'
        if action == CONSENT_ACTION_GRANT:
            result_message = (
                'Спасибо. Получение заявок и WhatsApp-уведомлений включено.'
            )
        else:
            result_message = (
                'Получение заявок и WhatsApp-уведомлений отключено.'
            )

    return render(
        request,
        'core/seller_whatsapp_consent.html',
        {
            'page_state': page_state,
            'seller_name': seller.name,
            'yes_label': SELLER_CONFIRM_YES_TEXT,
            'no_label': SELLER_CONFIRM_NO_TEXT,
            'result_message': result_message,
            'error_message': '',
        },
    )
