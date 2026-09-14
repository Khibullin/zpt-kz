from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Prefetch, Q
from django.http import QueryDict
from django.shortcuts import get_object_or_404

from control_panel.display import (
    SERVICE_MATCH_STATUS_LABELS,
    SERVICE_TYPE_LABELS,
    dash,
    mask_phone,
    summarize_names,
    vehicle_label,
    whatsapp_log_status_label,
    whatsapp_message_type_label,
)
from control_panel.periods import (
    DEFAULT_LIST_PERIOD,
    LIST_PERIOD_CHOICES,
    apply_created_range,
    normalize_period,
)
from control_panel.selectors.common import (
    WA_LOG_HISTORY_LIMIT,
    fetch_latest,
    first_value,
    paginate,
)
from service_requests.models import (
    Service,
    ServiceMatch,
    ServiceRequest,
    ServiceWhatsAppMessageLog,
)


@dataclass(frozen=True)
class ServiceRequestRow:
    pk: int
    created_at: datetime
    service_type: str
    services: str
    city: str
    vehicle: str
    matched: int
    notified: int


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


def list_service_requests(params: QueryDict) -> dict:
    period = normalize_period(
        params.get('period'),
        default=DEFAULT_LIST_PERIOD,
        allowed=tuple(key for key, _label in LIST_PERIOD_CHOICES),
    )
    queryset = apply_created_range(
        ServiceRequest.objects.annotate(
            matched=Count('servicematch', distinct=True),
            notified=Count(
                'wa_logs',
                filter=Q(wa_logs__status='sent', wa_logs__message_type='seller_request'),
                distinct=True,
            ),
        ),
        'created_at',
        period,
    )
    search = first_value(params, 'q')
    if search:
        query = Q(city__icontains=search) | Q(brand__icontains=search) | Q(model__icontains=search)
        if search.isdigit():
            query |= Q(pk=int(search))
        queryset = queryset.filter(query)
    city = first_value(params, 'city')
    if city:
        queryset = queryset.filter(city__icontains=city)
    service_type = first_value(params, 'service_type')
    if service_type in SERVICE_TYPE_LABELS:
        queryset = queryset.filter(service_type=service_type)
    else:
        service_type = ''
    service_id = first_value(params, 'service')
    if service_id.isdigit():
        queryset = queryset.filter(services__pk=int(service_id)).distinct()
    else:
        service_id = ''

    queryset = queryset.prefetch_related(_services_prefetch()).order_by('-created_at', '-id')
    page = paginate(queryset, params)
    rows = [
        ServiceRequestRow(
            pk=item.pk,
            created_at=item.created_at,
            service_type=_type_label(item.service_type),
            services=_service_preview(item),
            city=dash(item.city),
            vehicle=vehicle_label(item.brand, item.model),
            matched=item.matched,
            notified=item.notified,
        )
        for item in page.object_list
    ]
    cities = list(
        ServiceRequest.objects.exclude(city='')
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
        'service_options': list(Service.objects.order_by('name', 'id')),
        'filters': {
            'period': period,
            'q': search,
            'city': city,
            'service_type': service_type,
            'service': service_id,
        },
    }


def get_service_request_detail(pk: int) -> dict:
    request_obj = get_object_or_404(
        ServiceRequest.objects.prefetch_related(
            _services_prefetch(),
            Prefetch(
                'servicematch_set',
                queryset=ServiceMatch.objects.select_related('seller').order_by('id'),
            ),
        ),
        pk=pk,
    )
    matches = [
        {
            'seller_id': match.seller_id,
            'seller_name': dash(match.seller.name if match.seller_id else ''),
            'status': _match_status_label(match.status),
            'created_at': match.created_at,
        }
        for match in request_obj.servicematch_set.all()
    ]
    logs = [
        {
            'created_at': log.created_at,
            'seller_name': dash(log.seller.name if log.seller_id else ''),
            'status_label': whatsapp_log_status_label(log.status),
            'message_type': whatsapp_message_type_label(log.message_type),
        }
        for log in fetch_latest(
            ServiceWhatsAppMessageLog.objects.filter(request_id=request_obj.pk)
            .select_related('seller')
            .defer('error_text', 'response_json', 'phone')
            .order_by('-created_at', '-id'),
            limit=WA_LOG_HISTORY_LIMIT,
        )
    ]
    return {
        'service_request': request_obj,
        'vehicle': vehicle_label(request_obj.brand, request_obj.model),
        'service_type': _type_label(request_obj.service_type),
        'services': _service_names(request_obj),
        'masked_phone': mask_phone(request_obj.phone),
        'matches': matches,
        'logs': logs,
    }
