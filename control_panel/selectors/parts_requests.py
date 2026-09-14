from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.http import QueryDict
from django.shortcuts import get_object_or_404

from core.models import (
    Match,
    Request,
    RequestDispatch,
    SellerRequestAccess,
    SellerRequestPageEvent,
)
from control_panel.display import (
    dash,
    event_label,
    mask_phone,
    request_status_label,
    vehicle_label,
)
from control_panel.periods import DEFAULT_LIST_PERIOD, LIST_PERIOD_CHOICES, normalize_period
from control_panel.selectors.common import first_value, paginate


@dataclass(frozen=True)
class PartsRequestRow:
    pk: int
    created_at: datetime
    vehicle: str
    category: str
    city: str
    status: str
    sellers_received: int
    opened: int
    whatsapp: int
    called: int
    result: str


@dataclass(frozen=True)
class SellerReactionRow:
    seller_id: int | None
    seller_name: str
    sent_at: datetime | None
    opened: bool
    whatsapp: bool
    called: bool
    consent: str
    outcome: str
    last_action_at: datetime | None
    access_id: int | None


@dataclass(frozen=True)
class PageEventRow:
    created_at: datetime
    seller_name: str
    event_label: str
    access_id: int


def _base_queryset():
    return Request.objects.annotate(
        sellers_received=Count(
            'matches',
            filter=Q(matches__sent_at__isnull=False) | Q(matches__status='sent'),
            distinct=True,
        ),
        opened=Count(
            'seller_page_events__seller_id',
            filter=Q(seller_page_events__event_type='page_open'),
            distinct=True,
        ),
        whatsapp=Count(
            'seller_page_events__seller_id',
            filter=Q(seller_page_events__event_type='whatsapp_click'),
            distinct=True,
        ),
        called=Count(
            'seller_page_events__seller_id',
            filter=Q(seller_page_events__event_type='call_click'),
            distinct=True,
        ),
        stock_count=Count(
            'seller_page_events',
            filter=Q(seller_page_events__event_type='out_of_stock'),
            distinct=True,
        ),
        decline_count=Count(
            'seller_page_events',
            filter=Q(seller_page_events__event_type='cannot_fulfill'),
            distinct=True,
        ),
    )


def _result_label(stock_count: int, decline_count: int) -> str:
    parts = []
    if stock_count:
        parts.append('Нет в наличии')
    if decline_count:
        parts.append('Не могу выполнить')
    return ', '.join(parts) if parts else '—'


def _apply_filters(queryset, params: QueryDict):
    from control_panel.periods import apply_created_range

    period = normalize_period(
        params.get('period'),
        default=DEFAULT_LIST_PERIOD,
        allowed=tuple(key for key, _label in LIST_PERIOD_CHOICES),
    )
    queryset = apply_created_range(queryset, 'created_at', period)

    request_id = first_value(params, 'request_id')
    if request_id.isdigit():
        queryset = queryset.filter(pk=int(request_id))

    city = first_value(params, 'city')
    if city:
        queryset = queryset.filter(city__icontains=city)

    category = first_value(params, 'category')
    if category:
        queryset = queryset.filter(category__icontains=category)

    status = first_value(params, 'status')
    if status:
        queryset = queryset.filter(status=status)

    opened = first_value(params, 'opened')
    opened_exists = SellerRequestPageEvent.objects.filter(
        request_id=OuterRef('pk'),
        event_type='page_open',
    )
    if opened == 'yes':
        queryset = queryset.filter(Exists(opened_exists))
    elif opened == 'no':
        queryset = queryset.exclude(Exists(opened_exists))

    contact = first_value(params, 'contact')
    contact_exists = SellerRequestPageEvent.objects.filter(
        request_id=OuterRef('pk'),
        event_type__in=['whatsapp_click', 'call_click'],
    )
    if contact == 'yes':
        queryset = queryset.filter(Exists(contact_exists))
    elif contact == 'no':
        queryset = queryset.exclude(Exists(contact_exists))

    search = first_value(params, 'q')
    if search:
        query = (
            Q(brand__icontains=search)
            | Q(model__icontains=search)
            | Q(category__icontains=search)
            | Q(city__icontains=search)
        )
        if search.isdigit():
            query |= Q(pk=int(search))
        queryset = queryset.filter(query)

    return queryset


def list_parts_requests(params: QueryDict) -> dict:
    queryset = _apply_filters(_base_queryset(), params).order_by('-created_at', '-id')
    page = paginate(queryset, params)
    rows = [
        PartsRequestRow(
            pk=item.pk,
            created_at=item.created_at,
            vehicle=vehicle_label(item.brand, item.model),
            category=dash(item.category),
            city=dash(item.city),
            status=request_status_label(item.status),
            sellers_received=item.sellers_received,
            opened=item.opened,
            whatsapp=item.whatsapp,
            called=item.called,
            result=_result_label(item.stock_count, item.decline_count),
        )
        for item in page.object_list
    ]
    cities = list(
        Request.objects.exclude(city='')
        .order_by('city')
        .values_list('city', flat=True)
        .distinct()
    )
    categories = list(
        Request.objects.exclude(category='')
        .order_by('category')
        .values_list('category', flat=True)
        .distinct()
    )
    statuses = list(
        Request.objects.exclude(status='')
        .order_by('status')
        .values_list('status', flat=True)
        .distinct()
    )
    return {
        'rows': rows,
        'page': page.page,
        'querystring': page.querystring,
        'total': page.total,
        'cities': cities,
        'categories': categories,
        'statuses': [
            {'value': value, 'label': request_status_label(value)}
            for value in statuses
        ],
        'filters': {
            'period': normalize_period(
                params.get('period'),
                default=DEFAULT_LIST_PERIOD,
                allowed=tuple(key for key, _label in LIST_PERIOD_CHOICES),
            ),
            'request_id': first_value(params, 'request_id'),
            'city': first_value(params, 'city'),
            'category': first_value(params, 'category'),
            'status': first_value(params, 'status'),
            'opened': first_value(params, 'opened'),
            'contact': first_value(params, 'contact'),
            'q': first_value(params, 'q'),
        },
    }


def get_parts_request_detail(pk: int) -> dict:
    request_obj = get_object_or_404(
        Request.objects.prefetch_related(
            Prefetch(
                'matches',
                queryset=Match.objects.select_related('seller').order_by('id'),
            ),
            Prefetch(
                'dispatches',
                queryset=RequestDispatch.objects.select_related('seller').order_by(
                    'wave_number', 'position_number', 'id'
                ),
            ),
            Prefetch(
                'seller_access_links',
                queryset=SellerRequestAccess.objects.select_related('seller').order_by('id'),
            ),
            Prefetch(
                'seller_page_events',
                queryset=SellerRequestPageEvent.objects.select_related(
                    'seller', 'access'
                ).order_by('created_at', 'id'),
            ),
        ),
        pk=pk,
    )
    events = list(request_obj.seller_page_events.all())
    events_by_seller: dict[int, list] = {}
    for event in events:
        events_by_seller.setdefault(event.seller_id, []).append(event)

    access_by_seller = {
        access.seller_id: access
        for access in request_obj.seller_access_links.all()
        if access.seller_id
    }

    sent_by_seller = {
        match.seller_id: match.sent_at
        for match in request_obj.matches.all()
        if match.seller_id
    }
    for dispatch in request_obj.dispatches.all():
        if dispatch.seller_id and dispatch.seller_id not in sent_by_seller:
            sent_by_seller[dispatch.seller_id] = dispatch.sent_at
    seller_names = {}
    for match in request_obj.matches.all():
        if match.seller_id:
            seller_names[match.seller_id] = dash(match.seller.name if match.seller else '')
    for dispatch in request_obj.dispatches.all():
        if dispatch.seller_id and dispatch.seller_id not in seller_names:
            seller_names[dispatch.seller_id] = dash(
                dispatch.seller.name if dispatch.seller_id else ''
            )
    for access in request_obj.seller_access_links.all():
        if access.seller_id and access.seller_id not in seller_names:
            seller_names[access.seller_id] = dash(
                access.seller.name if access.seller_id else ''
            )
            sent_by_seller.setdefault(access.seller_id, None)

    reactions = []
    for seller_id, seller_name in seller_names.items():
        seller_events = events_by_seller.get(seller_id, [])
        types = {event.event_type for event in seller_events}
        consent = '—'
        if 'marketing_consent_yes' in types:
            consent = 'Да'
        elif 'marketing_consent_no' in types:
            consent = 'Нет'
        outcome = '—'
        if 'out_of_stock' in types:
            outcome = 'Нет в наличии'
        elif 'cannot_fulfill' in types:
            outcome = 'Не могу выполнить заявку'
        last_action = max((event.created_at for event in seller_events), default=None)
        access = access_by_seller.get(seller_id)
        reactions.append(
            SellerReactionRow(
                seller_id=seller_id,
                seller_name=seller_name,
                sent_at=sent_by_seller.get(seller_id),
                opened='page_open' in types,
                whatsapp='whatsapp_click' in types,
                called='call_click' in types,
                consent=consent,
                outcome=outcome,
                last_action_at=last_action,
                access_id=access.pk if access else None,
            )
        )

    event_rows = [
        PageEventRow(
            created_at=event.created_at,
            seller_name=dash(event.seller.name if event.seller_id else ''),
            event_label=event_label(event.event_type),
            access_id=event.access_id,
        )
        for event in events
    ]

    return {
        'parts_request': request_obj,
        'vehicle': vehicle_label(request_obj.brand, request_obj.model),
        'status_label': request_status_label(request_obj.status),
        'masked_phone': mask_phone(request_obj.phone),
        'reactions': reactions,
        'events': event_rows,
        'dispatches': list(request_obj.dispatches.all()),
        'access_count': len(request_obj.seller_access_links.all()),
    }
