from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from core.phone_utils import build_whatsapp_url, normalize_phone_for_whatsapp
from core.services.seller_request_access import find_seller_request_access
from core.services.seller_request_page_events import (
    CONSENT_CONFLICT_MESSAGES,
    EVENT_CALL_CLICK,
    EVENT_CANNOT_FULFILL,
    EVENT_OUT_OF_STOCK,
    EVENT_PAGE_OPEN,
    EVENT_WHATSAPP_CLICK,
    OUTCOME_CONFLICT_MESSAGES,
    OUTCOME_MESSAGES,
    can_record_page_event,
    get_seller_request_outcome,
    record_page_event,
    save_seller_request_consent_choice,
    save_seller_request_outcome,
)
from core.services.seller_buyer_request_consent import (
    request_consent_status,
    save_request_consent,
)
from core.models import (
    SELLER_REQUEST_PAGE_EVENT_REQUESTS_YES,
    SELLER_REQUEST_PAGE_EVENT_REQUESTS_NO,
)
from core.services.seller_whatsapp_consent import (
    SellerWhatsAppConsentError,
    seller_needs_request_page_marketing_prompt,
)

TEMPLATE = 'core/seller_request_link.html'

ACTION_CONSENT_YES = 'consent_yes'
ACTION_CONSENT_NO = 'consent_no'
ACTION_REQUESTS_YES = 'requests_yes'
ACTION_REQUESTS_NO = 'requests_no'
ACTION_UNAVAILABLE = 'unavailable'
ACTION_DECLINE = 'decline'
ACTION_WHATSAPP_CLICK = 'whatsapp_click'
ACTION_CALL_CLICK = 'call_click'

ACTION_TO_OUTCOME = {
    ACTION_UNAVAILABLE: EVENT_OUT_OF_STOCK,
    ACTION_DECLINE: EVENT_CANNOT_FULFILL,
}

JSON_ACTIONS = frozenset({
    ACTION_CONSENT_YES,
    ACTION_CONSENT_NO,
    ACTION_REQUESTS_YES,
    ACTION_REQUESTS_NO,
    ACTION_UNAVAILABLE,
    ACTION_DECLINE,
    ACTION_WHATSAPP_CLICK,
    ACTION_CALL_CLICK,
})


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    return response


def _json(payload: dict, *, status: int = 200):
    return _no_store(JsonResponse(payload, status=status))


def _buyer_whatsapp_prefill(req) -> str:
    vehicle = ' '.join(
        part for part in ((req.brand or '').strip(), (req.model or '').strip()) if part
    )
    detail = ', '.join(part for part in (vehicle, (req.category or '').strip()) if part)
    text = f'Здравствуйте! Пишу по заявке №{req.pk} на ZPT.KZ'
    if detail:
        text += f': {detail}'
    return text + '.'


def _tel_url(phone) -> str:
    digits = normalize_phone_for_whatsapp(phone)
    if not digits:
        return ''
    return f'tel:+{digits}'


def _contact_urls(req) -> dict:
    return {
        'whatsapp_url': build_whatsapp_url(req.phone, _buyer_whatsapp_prefill(req)),
        'tel_url': _tel_url(req.phone),
    }


def _page_context(page_state: str, *, access=None, req=None) -> dict:
    context = {
        'page_state': page_state,
        'request_number': '',
        'vehicle': '',
        'year': '',
        'vin': '',
        'category': '',
        'city': '',
        'comment': '',
        'whatsapp_url': '',
        'tel_url': '',
        'needs_consent': False,
        'requests_consent_status': '',
        'can_decline': False,
        'match_status': '',
        'decline_message': '',
        'photos': [],
    }
    if page_state != 'valid' or req is None or access is None:
        return context

    outcome = None
    if access.seller_id:
        outcome = get_seller_request_outcome(
            request_id=req.pk,
            seller_id=access.seller_id,
        )
    outcome_type = outcome.event_type if outcome else ''
    vehicle = ' '.join(
        part for part in ((req.brand or '').strip(), (req.model or '').strip()) if part
    )
    year = str(req.year or '').strip()
    vin = (req.vin or '').strip()
    context.update({
        'filter_token': bool(access.seller_id and access.seller.is_active),
        'request_number': str(req.pk),
        'vehicle': vehicle,
        'year': year,
        'vin': vin,
        'category': (req.category or '').strip(),
        'city': (req.city or '').strip(),
        'comment': (req.description or '').strip(),
        **_contact_urls(req),
        'needs_consent': seller_needs_request_page_marketing_prompt(access.seller),
        'requests_consent_status': request_consent_status(access.seller),
        'can_decline': bool(access.seller_id) and not outcome_type,
        'match_status': outcome_type,
        'decline_message': OUTCOME_MESSAGES.get(outcome_type, ''),
        'photos': list(req.photos.all()),
    })
    from core.services.request_connections import enabled, telegram_contact
    if enabled():
        context['buyer_telegram_url'] = telegram_contact(req.buyer_telegram)
        context['connection_request_id'] = req.pk
        context['buyer_has_telegram'] = bool(context['buyer_telegram_url'])
    return context


def _page(http_request, *, page_state: str, status: int, access=None, req=None, direct_manual=False):
    return _no_store(
        render(
            http_request,
            TEMPLATE,
            {**_page_context(page_state, access=access, req=req), 'direct_manual': direct_manual},
            status=status,
        )
    )


def _maybe_record_page_open(http_request, access, req):
    if http_request.method != 'GET':
        return
    if not can_record_page_event(access, req):
        return
    record_page_event(
        access,
        EVENT_PAGE_OPEN,
        request=req,
        seller=access.seller,
    )


def _handle_contact_click(access, req, event_type: str):
    urls = _contact_urls(req)
    target_url = urls['whatsapp_url'] if event_type == EVENT_WHATSAPP_CLICK else urls['tel_url']
    if not target_url:
        return _json({'ok': False, 'error': 'Контакт покупателя недоступен.'}, status=400)
    from core.services import request_connections as connections
    if connections.enabled():
        connections.record(req, access.seller, 'seller', event_type)
    if can_record_page_event(access, req):
        event, _created = record_page_event(
            access,
            event_type,
            request=req,
            seller=access.seller,
        )
        if event is None:
            return _json({'ok': False, 'error': 'Не удалось сохранить действие.'}, status=400)
    return _json({'ok': True, **urls})


def _handle_consent(access, req, granted: bool):
    urls = _contact_urls(req)
    if access.seller is None:
        return _json(
            {'ok': False, 'error': 'Согласие недоступно для этой ссылки.', **urls},
            status=400,
        )
    try:
        event, accepted, _created = save_seller_request_consent_choice(
            access,
            granted,
            request=req,
            seller=access.seller,
            evidence_reference=(
                f'seller_request_page:access:{access.pk}:request:{req.pk}'
            ),
        )
    except SellerWhatsAppConsentError as exc:
        return _json({'ok': False, 'error': exc.message, **urls}, status=400)
    if event is None:
        return _json({'ok': False, 'error': 'Не удалось сохранить выбор.', **urls}, status=400)
    if not accepted:
        return _json(
            {
                'ok': False,
                'error': CONSENT_CONFLICT_MESSAGES.get(
                    event.event_type,
                    'Выбор уже сохранён.',
                ),
                'consent': event.event_type,
                'needs_consent': False,
                **urls,
            },
            status=409,
        )
    return _json({
        'ok': True,
        'needs_consent': False,
        'consent': event.event_type,
        **urls,
    })


def _handle_request_subscription(access, req, granted: bool):
    if access.seller is None or not access.seller.is_active:
        return _json({'ok': False, 'error': 'Согласие недоступно.'}, status=400)
    try:
        consent = save_request_consent(
            access.seller,
            granted=granted,
            evidence_reference=f'buyer_requests:access:{access.pk}:request:{req.pk}',
        )
    except ValueError as exc:
        return _json({'ok': False, 'error': str(exc)}, status=400)
    record_page_event(
        access,
        SELLER_REQUEST_PAGE_EVENT_REQUESTS_YES if granted
        else SELLER_REQUEST_PAGE_EVENT_REQUESTS_NO,
        request=req,
        seller=access.seller,
    )
    return _json({'ok': True, 'requests_consent_status': consent.status})


def _handle_outcome(access, req, action: str):
    if access.seller is None:
        return _json({'ok': False, 'error': 'Действие недоступно.'}, status=400)
    outcome = ACTION_TO_OUTCOME[action]
    event, accepted, _created = save_seller_request_outcome(
        access,
        outcome,
        request=req,
        seller=access.seller,
    )
    if event is None:
        return _json({'ok': False, 'error': 'Не удалось сохранить ответ.'}, status=400)
    if not accepted:
        stored = event.event_type
        return _json(
            {
                'ok': False,
                'error': OUTCOME_CONFLICT_MESSAGES.get(stored, 'Ответ уже сохранён.'),
                'outcome': stored,
                'decline_message': OUTCOME_MESSAGES.get(stored, ''),
                'can_decline': False,
            },
            status=409,
        )
    from core.services import request_connections as connections
    if connections.enabled():
        connections.record(req, access.seller, 'seller', outcome)
    return _json({
        'ok': True,
        'outcome': event.event_type,
        'decline_message': OUTCOME_MESSAGES.get(event.event_type, ''),
        'can_decline': False,
    })


def _handle_post(http_request, access, req):
    action = str(http_request.POST.get('action') or '').strip()
    if action not in JSON_ACTIONS:
        return _json({'ok': False, 'error': 'Неизвестное действие.'}, status=400)
    if action == ACTION_WHATSAPP_CLICK:
        return _handle_contact_click(access, req, EVENT_WHATSAPP_CLICK)
    if action == ACTION_CALL_CLICK:
        return _handle_contact_click(access, req, EVENT_CALL_CLICK)
    if action == ACTION_CONSENT_YES:
        return _handle_consent(access, req, True)
    if action == ACTION_CONSENT_NO:
        return _handle_consent(access, req, False)
    if action == ACTION_REQUESTS_YES:
        return _handle_request_subscription(access, req, True)
    if action == ACTION_REQUESTS_NO:
        return _handle_request_subscription(access, req, False)
    return _handle_outcome(access, req, action)


@ensure_csrf_cookie
@require_http_methods(['GET', 'HEAD', 'POST'])
def seller_request_link(request, token):
    access = find_seller_request_access(token)
    if access is None:
        if request.method == 'POST':
            return _json({'ok': False, 'error': 'Ссылка недействительна.'}, status=404)
        return _page(request, page_state='invalid', status=404)
    if access.is_expired():
        if request.method == 'POST':
            return _json({'ok': False, 'error': 'Срок действия ссылки истёк.'}, status=410)
        from core.services.request_connections import enabled
        if enabled():
            return render(request, 'core/connections/expired.html', status=410)
        return _page(request, page_state='expired', status=410)
    req = access.request
    from core.services import request_connections as connections
    # A private manual dispatch token identifies only its bound seller and request.
    # It does not sign in, claim a store, or grant access to other requests.
    from core.models import RequestDispatch
    direct_manual = bool(req and access.seller_id and RequestDispatch.objects.filter(
        request=req, seller=access.seller, delivery_channel=RequestDispatch.DELIVERY_MANUAL,
        status__in=[RequestDispatch.STATUS_PREPARED, RequestDispatch.STATUS_SENT],
    ).exclude(permission_evidence='').exists())
    if direct_manual:
        seller = access.seller
        if (not seller.is_active or seller.is_paused or seller.catalog_only
                or not req.contact_consent_at or not connections.buyer_access_valid(req)
                or req.purchase_result in ('purchased', 'closed')):
            if request.method == 'POST':
                return _json({'ok': False, 'error': 'Заявка недоступна.'}, status=403)
            return _page(request, page_state='invalid', status=403)
    if connections.enabled() and req is not None and not direct_manual:
        from core.services.seller_identity import get_logged_request_seller
        seller = get_logged_request_seller(request)
        if (not seller or not request.user.is_authenticated or seller.user_id != request.user.pk
                or seller.pk != access.seller_id or not connections.can_open_contacts(req, seller)):
            if request.method == 'GET' and seller and seller.pk == access.seller_id and seller.contact_access_verified and req.contact_consent_at and not req.access_revoked and req.purchase_result not in ('purchased', 'closed'):
                from django.shortcuts import redirect
                return redirect('connection_detail', req_id=req.pk)
            if request.method == 'GET':
                return render(request, 'core/connections/connect.html', {'req_id': req.pk, 'source': 'whatsapp'}, status=403)
            return _json({'ok': False, 'error': 'Войдите под допущенным продавцом и подтвердите интерес к заявке.'}, status=403)
        if request.method == 'GET':
            connections.record(req, seller, 'seller', 'opened')
    if req is None:
        if request.method == 'POST':
            return _json({'ok': False, 'error': 'Заявка недоступна.'}, status=404)
        return _page(request, page_state='invalid', status=404)
    if request.method == 'POST':
        return _handle_post(request, access, req)
    _maybe_record_page_open(request, access, req)
    return _page(request, page_state='valid', status=200, access=access, req=req, direct_manual=direct_manual)
