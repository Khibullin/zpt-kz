import os
import uuid
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from django.db.models import Count, Q
from django.urls import reverse

from core.models import BuyerPortalAccess, Match, Request, RequestDispatch
from core.request_dispatch_service import (
    build_successful_whatsapp_log_index,
    resolve_whatsapp_status,
)


BUYER_SELLERS_VISIBLE_COUNT = 8

BUYER_STATUS_SENT = 'Заявка отправлена продавцу'
BUYER_STATUS_PENDING = 'Ожидает отправки'
BUYER_STATUS_DIRECT = 'Можно написать продавцу напрямую'

BUYER_STATUS_LABELS = {
    'sent': BUYER_STATUS_SENT,
    'pending': BUYER_STATUS_PENDING,
    'error': BUYER_STATUS_DIRECT,
}

BUYER_STATUS_SORT_ORDER = {
    'sent': 0,
    'pending': 1,
    'error': 2,
}


def normalize_buyer_phone(phone):
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())


def _public_base_url():
    return os.getenv('PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')


def _append_utm(url, campaign='buyer_request_created'):
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({
        'utm_source': 'whatsapp',
        'utm_medium': 'transactional',
        'utm_campaign': campaign,
    })
    return urlunparse(parsed._replace(query=urlencode(query)))


def _absolute_url(relative_path, with_utm=False, utm_campaign='buyer_request_created'):
    path = relative_path if relative_path.startswith('/') else f'/{relative_path}'
    url = f'{_public_base_url()}{path}'
    if with_utm:
        url = _append_utm(url, campaign=utm_campaign)
    return url


def ensure_buyer_portal_access(phone):
    phone_normalized = normalize_buyer_phone(phone)
    if not phone_normalized:
        return None
    portal, _ = BuyerPortalAccess.objects.get_or_create(
        phone_normalized=phone_normalized,
        defaults={'access_token': uuid.uuid4()},
    )
    return portal


def phone_lookup_q(normalized_phone):
    q = Q(phone=normalized_phone)
    if normalized_phone.startswith('7') and len(normalized_phone) == 11:
        q |= Q(phone='8' + normalized_phone[1:])
    return q


def buyer_requests_queryset(portal):
    return (
        Request.objects.filter(phone_lookup_q(portal.phone_normalized))
        .annotate(
            photos_count=Count('photos', distinct=True),
            sellers_count=Count('dispatches', distinct=True),
        )
        .order_by('-created_at')
    )


def buyer_dispatch_status_label(
    dispatch,
    match=None,
    *,
    success_log_keys=None,
):
    status = resolve_whatsapp_status(
        dispatch,
        match,
        success_log_keys=success_log_keys,
    )
    return BUYER_STATUS_LABELS[status]


def build_request_sellers(req):
    dispatches = list(
        req.dispatches.select_related('seller').order_by('position_number')
    )
    match_map = {
        match.seller_id: match
        for match in Match.objects.filter(request=req)
    }
    success_log_keys = build_successful_whatsapp_log_index(req.id)

    sellers = []
    for dispatch in dispatches:
        seller = dispatch.seller
        match = match_map.get(seller.id)
        whatsapp_status = resolve_whatsapp_status(
            dispatch,
            match,
            success_log_keys=success_log_keys,
        )
        sellers.append({
            'name': seller.name.strip() if seller.name else 'Продавец',
            'city': seller.city,
            'status_label': BUYER_STATUS_LABELS[whatsapp_status],
            'whatsapp_status': whatsapp_status,
            'seller_id': seller.id,
            'whatsapp': seller.whatsapp,
            '_sort_key': (
                BUYER_STATUS_SORT_ORDER[whatsapp_status],
                dispatch.position_number,
            ),
        })

    sellers.sort(key=lambda item: item['_sort_key'])
    for seller in sellers:
        seller.pop('_sort_key', None)

    visible_count = BUYER_SELLERS_VISIBLE_COUNT
    hidden_count = max(len(sellers) - visible_count, 0)
    return {
        'items': sellers,
        'visible': sellers[:visible_count],
        'hidden': sellers[visible_count:],
        'hidden_count': hidden_count,
        'total': len(sellers),
    }


REQUEST_STATUS_LABELS = {
    'new': 'Новая',
    'sent': 'Отправлена продавцам',
    'no_sellers': 'Продавцы не найдены',
}


def request_page_url(req, with_utm=False):
    relative = reverse(
        'view_request_status_public',
        kwargs={'req_id': req.id, 'access_token': req.access_token},
    )
    return _absolute_url(relative, with_utm=with_utm)


def buyer_history_url(req, with_utm=False):
    portal = ensure_buyer_portal_access(req.phone)
    if not portal:
        return _absolute_url('/', with_utm=with_utm)
    relative = reverse(
        'view_buyer_request_history_public',
        kwargs={'access_token': portal.access_token},
    )
    return _absolute_url(relative, with_utm=with_utm)


def buyer_request_whatsapp_url_suffix(req):
    return f'{req.id}/{req.access_token}/'


def buyer_history_whatsapp_url_suffix(req):
    portal = ensure_buyer_portal_access(req.phone)
    if not portal:
        return '-'
    return f'{portal.access_token}/'


def home_page_url(with_utm=False):
    return _absolute_url(reverse('home'), with_utm=with_utm)


def new_request_url(req=None, with_utm=False):
    path = '/request-parts/'
    if req is not None:
        path = repeat_request_path(req)
    return _absolute_url(path, with_utm=with_utm)


def repeat_request_path(req):
    params = {
        'transport': req.transport_type or 'car',
        'country': req.country or '',
        'brand': req.brand or '',
        'model': req.model or '',
        'category': req.category or '',
        'city': req.city or '',
        'phone': req.phone or '',
        'search_scope': req.search_scope or 'city',
        'article': req.article or '',
        'description': req.description or '',
    }
    if req.selected_cities:
        params['selected_cities'] = req.selected_cities
    query = urlencode({k: v for k, v in params.items() if v})
    return f'/request-parts/?{query}' if query else '/request-parts/'


def repeat_request_url(req, with_utm=False):
    return _absolute_url(repeat_request_path(req), with_utm=with_utm)
