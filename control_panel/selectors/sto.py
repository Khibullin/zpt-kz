from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, F, Max, Prefetch, Q
from django.http import QueryDict
from django.shortcuts import get_object_or_404

from control_panel.display import (
    SERVICE_MATCH_STATUS_LABELS,
    SERVICE_TYPE_LABELS,
    STO_SORT_CHOICES,
    dash,
    normalize_sto_sort,
    summarize_names,
    whatsapp_log_status_label,
    whatsapp_message_type_label,
)
from control_panel.selectors.common import (
    STO_MATCH_HISTORY_LIMIT,
    WA_LOG_HISTORY_LIMIT,
    fetch_latest,
    first_value,
    paginate,
)
from service_requests.models import (
    Service,
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
    last_activity: datetime | None


def _ordered_service_names(holder) -> list[str]:
    return [item.name for item in holder.services.all()]


def _service_names(holder) -> str:
    names = _ordered_service_names(holder)
    return ', '.join(names) if names else '—'


def _service_preview(holder) -> str:
    return summarize_names(_ordered_service_names(holder))


def _type_label(value: str) -> str:
    return SERVICE_TYPE_LABELS.get(value) or 'Нет данных'


def _match_status_label(value: str) -> str:
    return SERVICE_MATCH_STATUS_LABELS.get(value) or 'Нет данных'


def _services_prefetch():
    return Prefetch('services', queryset=Service.objects.order_by('name', 'id'))


def list_sto(params: QueryDict) -> dict:
    queryset = ServiceSeller.objects.annotate(
        received=Count('servicematch', distinct=True),
        last_activity=Max('wa_logs__created_at'),
    ).prefetch_related(_services_prefetch())
    city = first_value(params, 'city')
    if city:
        queryset = queryset.filter(city__icontains=city)
    district = first_value(params, 'district')
    if district:
        queryset = queryset.filter(district__icontains=district)
    seller_type = first_value(params, 'seller_type')
    if seller_type in SERVICE_TYPE_LABELS:
        queryset = queryset.filter(seller_type=seller_type)
    else:
        seller_type = ''
    service_id = first_value(params, 'service')
    if service_id.isdigit():
        queryset = queryset.filter(services__pk=int(service_id)).distinct()
    else:
        service_id = ''
    active = first_value(params, 'active')
    if active == 'yes':
        queryset = queryset.filter(is_active=True)
    elif active == 'no':
        queryset = queryset.filter(is_active=False)
    search = first_value(params, 'q')
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(city__icontains=search))

    sort = normalize_sto_sort(first_value(params, 'sort'))
    if sort == 'city':
        queryset = queryset.order_by('city', 'name', 'id')
    elif sort == 'activity':
        queryset = queryset.order_by(
            F('last_activity').desc(nulls_last=True), 'name', 'id'
        )
    else:
        queryset = queryset.order_by('name', 'id')

    page = paginate(queryset, params)
    rows = [
        StoRow(
            pk=item.pk,
            name=item.name,
            city=dash(item.city),
            district=dash(item.district),
            seller_type=_type_label(item.seller_type),
            services=_service_preview(item),
            is_active=item.is_active,
            receive_requests=item.receive_requests,
            is_paused=item.is_paused,
            received=item.received,
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
    districts = list(
        ServiceSeller.objects.exclude(district='')
        .order_by('district')
        .values_list('district', flat=True)
        .distinct()
    )
    catalog = list(Service.objects.order_by('name', 'id'))
    return {
        'rows': rows,
        'page': page.page,
        'querystring': page.querystring,
        'total': page.total,
        'cities': cities,
        'districts': districts,
        'service_options': catalog,
        'sort_choices': STO_SORT_CHOICES,
        'filters': {
            'city': city,
            'district': district,
            'seller_type': seller_type,
            'service': service_id,
            'active': active,
            'q': search,
            'sort': sort,
        },
    }


def get_sto_detail(pk: int) -> dict:
    seller = get_object_or_404(
        ServiceSeller.objects.prefetch_related(_services_prefetch()),
        pk=pk,
    )
    matches = [
        {
            'request_id': match.request_id,
            'status': _match_status_label(match.status),
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
            'message_type': whatsapp_message_type_label(log.message_type),
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
        'seller_type': _type_label(seller.seller_type),
        'services': _service_names(seller),
        'matches': matches,
        'logs': logs,
        'admin_url': f'/admin/service_requests/serviceseller/{seller.pk}/change/',
    }
