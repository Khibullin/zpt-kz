from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Max, Q
from django.http import QueryDict
from django.shortcuts import get_object_or_404

from control_panel.display import (
    SERVICE_MATCH_STATUS_LABELS,
    SERVICE_TYPE_LABELS,
    dash,
    whatsapp_log_status_label,
)
from control_panel.selectors.common import (
    STO_MATCH_HISTORY_LIMIT,
    WA_LOG_HISTORY_LIMIT,
    fetch_latest,
    first_value,
    paginate,
)
from service_requests.models import (
    ServiceMatch,
    ServiceSeller,
    ServiceWhatsAppMessageLog,
)


@dataclass(frozen=True)
class StoRow:
    pk: int
    name: str
    city: str
    district: str
    seller_type: str
    services: str
    is_active: bool
    receive_requests: bool
    is_paused: bool
    received: int
    replies: str
    last_activity: datetime | None


def _service_names(seller: ServiceSeller) -> str:
    names = [item.name for item in seller.services.all()]
    return dash(', '.join(names))


def list_sto(params: QueryDict) -> dict:
    queryset = ServiceSeller.objects.annotate(
        received=Count('servicematch', distinct=True),
        last_activity=Max('wa_logs__created_at'),
    ).prefetch_related('services')
    city = first_value(params, 'city')
    if city:
        queryset = queryset.filter(city__icontains=city)
    active = first_value(params, 'active')
    if active == 'yes':
        queryset = queryset.filter(is_active=True)
    elif active == 'no':
        queryset = queryset.filter(is_active=False)
    search = first_value(params, 'q')
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(city__icontains=search))
    queryset = queryset.order_by('name', 'id')
    page = paginate(queryset, params)
    rows = [
        StoRow(
            pk=item.pk,
            name=item.name,
            city=dash(item.city),
            district=dash(item.district),
            seller_type=SERVICE_TYPE_LABELS.get(item.seller_type, item.seller_type),
            services=_service_names(item),
            is_active=item.is_active,
            receive_requests=item.receive_requests,
            is_paused=item.is_paused,
            received=item.received,
            replies='Нет данных',
            last_activity=item.last_activity,
        )
        for item in page.object_list
    ]
    cities = list(
        ServiceSeller.objects.exclude(city='')
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
        'filters': {'city': city, 'active': active, 'q': search},
        'gaps': [
            'Ответы СТО как отдельное событие не хранятся.',
            'Последняя активность — max(created_at) WhatsApp-лога, если он есть.',
        ],
    }


def get_sto_detail(pk: int) -> dict:
    seller = get_object_or_404(
        ServiceSeller.objects.prefetch_related('services'),
        pk=pk,
    )
    matches = [
        {
            'request_id': match.request_id,
            'status': SERVICE_MATCH_STATUS_LABELS.get(match.status, match.status),
            'created_at': match.created_at,
            'city': dash(match.request.city if match.request_id else ''),
        }
        for match in fetch_latest(
            ServiceMatch.objects.filter(seller_id=seller.pk)
            .select_related('request')
            .order_by('-created_at', '-id'),
            limit=STO_MATCH_HISTORY_LIMIT,
        )
    ]
    logs = [
        {
            'created_at': log.created_at,
            'status_label': whatsapp_log_status_label(log.status),
            'message_type': log.message_type,
            'request_id': log.request_id,
        }
        for log in fetch_latest(
            ServiceWhatsAppMessageLog.objects.filter(seller_id=seller.pk)
            .defer('error_text', 'response_json', 'phone')
            .order_by('-created_at', '-id'),
            limit=WA_LOG_HISTORY_LIMIT,
        )
    ]
    return {
        'seller': seller,
        'seller_type': SERVICE_TYPE_LABELS.get(seller.seller_type, seller.seller_type),
        'services': _service_names(seller),
        'matches': matches,
        'logs': logs,
        'replies': 'Нет данных',
        'admin_url': f'/admin/service_requests/serviceseller/{seller.pk}/change/',
        'gaps': [
            'Service — справочник услуг, ServiceSeller — профиль СТО. Они не объединены в одну модель.',
            'Исполнители как отдельная сущность отсутствуют: карточка показывает профиль СТО.',
            'Телефон клиента и WhatsApp СТО в Control не выводим.',
        ],
    }
