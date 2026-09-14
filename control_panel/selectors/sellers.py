from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Exists, Max, OuterRef, Prefetch, Q
from django.http import QueryDict
from django.shortcuts import get_object_or_404

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    Match,
    Seller,
    SellerContactConsent,
    SellerRequestPageEvent,
)
from core.phone_utils import normalize_kz_phone
from core.services.seller_whatsapp_consent import get_seller_whatsapp_marketing_consent
from control_panel.display import dash, event_label, marketing_consent_label
from control_panel.selectors.common import (
    SELLER_EVENT_HISTORY_LIMIT,
    SELLER_MATCH_HISTORY_LIMIT,
    fetch_latest,
    first_value,
    paginate,
)


@dataclass(frozen=True)
class SellerRow:
    pk: int
    name: str
    city: str
    categories: str
    is_active: bool
    receive_requests: bool
    is_paused: bool
    consent_status: str
    consent_label: str
    sent: int
    opened: int
    whatsapp: int
    called: int
    declined: int
    last_activity: datetime | None


def _category_text(seller: Seller) -> str:
    if seller.all_categories:
        return 'Все категории'
    names = [item.name for item in seller.selected_categories.all()]
    if names:
        return ', '.join(names)
    return dash(seller.category)


def _consent_for_seller(seller: Seller) -> SellerContactConsent | None:
    consents = getattr(seller, 'marketing_consents', None)
    if consents is None:
        return get_seller_whatsapp_marketing_consent(seller)
    phone = normalize_kz_phone(seller.whatsapp)
    for consent in consents:
        if phone and consent.phone_normalized == phone:
            return consent
    return consents[0] if consents else None


def _consent_prefetch():
    return Prefetch(
        'contact_consents',
        queryset=SellerContactConsent.objects.filter(
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        ).order_by('-updated_at', '-id'),
        to_attr='marketing_consents',
    )


def _annotated_sellers():
    return Seller.objects.annotate(
        sent_count=Count(
            'matches',
            filter=Q(matches__sent_at__isnull=False) | Q(matches__status='sent'),
            distinct=True,
        ),
        opened_count=Count(
            'request_page_events',
            filter=Q(request_page_events__event_type='page_open'),
            distinct=True,
        ),
        whatsapp_count=Count(
            'request_page_events',
            filter=Q(request_page_events__event_type='whatsapp_click'),
            distinct=True,
        ),
        call_count=Count(
            'request_page_events',
            filter=Q(request_page_events__event_type='call_click'),
            distinct=True,
        ),
        declined_count=Count(
            'request_page_events',
            filter=Q(
                request_page_events__event_type__in=['out_of_stock', 'cannot_fulfill']
            ),
            distinct=True,
        ),
        last_activity=Max('request_page_events__created_at'),
    )


def list_sellers(params: QueryDict) -> dict:
    queryset = _annotated_sellers().prefetch_related(
        'selected_categories',
        _consent_prefetch(),
    )
    city = first_value(params, 'city')
    if city:
        queryset = queryset.filter(city__icontains=city)
    active = first_value(params, 'active')
    if active == 'yes':
        queryset = queryset.filter(is_active=True)
    elif active == 'no':
        queryset = queryset.filter(is_active=False)
    receive = first_value(params, 'receive')
    if receive == 'yes':
        queryset = queryset.filter(receive_requests=True)
    elif receive == 'no':
        queryset = queryset.filter(receive_requests=False)
    consent = first_value(params, 'consent')
    granted = SellerContactConsent.objects.filter(
        seller_id=OuterRef('pk'),
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        status=CONTACT_CONSENT_STATUS_GRANTED,
    )
    revoked = SellerContactConsent.objects.filter(
        seller_id=OuterRef('pk'),
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        status=CONTACT_CONSENT_STATUS_REVOKED,
    )
    if consent == 'granted':
        queryset = queryset.filter(Exists(granted))
    elif consent == 'revoked':
        queryset = queryset.filter(Exists(revoked))
    elif consent == 'unknown':
        queryset = queryset.exclude(Exists(granted)).exclude(Exists(revoked))
    opened = first_value(params, 'opened')
    opened_exists = SellerRequestPageEvent.objects.filter(
        seller_id=OuterRef('pk'),
        event_type='page_open',
    )
    if opened == 'yes':
        queryset = queryset.filter(Exists(opened_exists))
    elif opened == 'no':
        queryset = queryset.exclude(Exists(opened_exists))
    activity = first_value(params, 'activity')
    activity_exists = SellerRequestPageEvent.objects.filter(seller_id=OuterRef('pk'))
    if activity == 'yes':
        queryset = queryset.filter(Exists(activity_exists))
    elif activity == 'no':
        queryset = queryset.exclude(Exists(activity_exists))
    category = first_value(params, 'category')
    if category:
        queryset = queryset.filter(
            Q(all_categories=True)
            | Q(selected_categories__name__icontains=category)
            | Q(category__icontains=category)
        ).distinct()
    search = first_value(params, 'q')
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(city__icontains=search))

    queryset = queryset.order_by('name', 'id')
    page = paginate(queryset, params)
    rows = []
    for seller in page.object_list:
        record = _consent_for_seller(seller)
        status = record.status if record else ''
        rows.append(
            SellerRow(
                pk=seller.pk,
                name=seller.name,
                city=dash(seller.city),
                categories=_category_text(seller),
                is_active=seller.is_active,
                receive_requests=seller.receive_requests,
                is_paused=seller.is_paused,
                consent_status=status or 'absent',
                consent_label=marketing_consent_label(status),
                sent=seller.sent_count,
                opened=seller.opened_count,
                whatsapp=seller.whatsapp_count,
                called=seller.call_count,
                declined=seller.declined_count,
                last_activity=seller.last_activity,
            )
        )
    cities = list(
        Seller.objects.exclude(city='')
        .order_by('city')
        .values_list('city', flat=True)
        .distinct()
    )
    return {
        'rows': rows,
        'page': page.page,
        'querystring': page.querystring,
        'total': page.total,
        'cities': cities,
        'filters': {
            'city': city,
            'active': active,
            'receive': receive,
            'consent': consent,
            'opened': opened,
            'activity': activity,
            'category': category,
            'q': search,
        },
    }


def get_seller_detail(pk: int) -> dict:
    seller = get_object_or_404(
        _annotated_sellers().prefetch_related(
            'selected_categories',
            'selected_brands',
            _consent_prefetch(),
        ),
        pk=pk,
    )
    consent = _consent_for_seller(seller)
    requests = [
        {
            'request_id': match.request_id,
            'created_at': match.created_at,
            'sent_at': match.sent_at,
            'status': match.status,
            'vehicle': f'{match.request.brand} {match.request.model}'.strip()
            if match.request_id
            else '—',
        }
        for match in fetch_latest(
            Match.objects.filter(seller_id=seller.pk)
            .select_related('request')
            .order_by('-created_at', '-id'),
            limit=SELLER_MATCH_HISTORY_LIMIT,
        )
    ]
    events = [
        {
            'created_at': event.created_at,
            'event_label': event_label(event.event_type),
            'request_id': event.request_id,
            'access_id': event.access_id,
        }
        for event in fetch_latest(
            SellerRequestPageEvent.objects.filter(seller_id=seller.pk)
            .select_related('request')
            .order_by('-created_at', '-id'),
            limit=SELLER_EVENT_HISTORY_LIMIT,
        )
    ]
    return {
        'seller': seller,
        'categories': _category_text(seller),
        'brands': ', '.join(item.name for item in seller.selected_brands.all()) or '—',
        'consent': consent,
        'consent_label': marketing_consent_label(consent.status if consent else ''),
        'sent': seller.sent_count,
        'opened': seller.opened_count,
        'whatsapp': seller.whatsapp_count,
        'called': seller.call_count,
        'declined': seller.declined_count,
        'last_activity': seller.last_activity,
        'requests': requests,
        'events': events,
        'admin_url': f'/admin/core/seller/{seller.pk}/change/',
    }
