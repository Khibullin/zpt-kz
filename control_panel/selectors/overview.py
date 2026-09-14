from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Exists, OuterRef, Q

from core.models import (
    Request,
    RequestDispatch,
    SellerRequestPageEvent,
)
from control_panel.display import (
    dash,
    event_label,
    request_status_label,
    vehicle_label,
)
from control_panel.periods import apply_created_range
from service_requests.models import ServiceRequest, ServiceSeller


@dataclass(frozen=True)
class KpiItem:
    key: str
    value: int
    label: str
    hint: str


@dataclass(frozen=True)
class RecentRequestRow:
    pk: int
    created_at: datetime
    title: str
    city: str
    status: str
    url_name: str


@dataclass(frozen=True)
class RecentEventRow:
    created_at: datetime
    seller_name: str
    event_label: str
    request_id: int


@dataclass(frozen=True)
class AttentionItem:
    title: str
    detail: str
    url_name: str
    object_id: int | None = None


_KPI_EVENT_TYPES = (
    'page_open',
    'whatsapp_click',
    'call_click',
    'out_of_stock',
    'cannot_fulfill',
    'marketing_consent_yes',
    'marketing_consent_no',
)


def _event_counts(period: str) -> dict[str, int]:
    row = apply_created_range(
        SellerRequestPageEvent.objects.all(),
        'created_at',
        period,
    ).aggregate(
        **{
            event_type: Count('id', filter=Q(event_type=event_type))
            for event_type in _KPI_EVENT_TYPES
        }
    )
    return {
        event_type: int(row.get(event_type) or 0)
        for event_type in _KPI_EVENT_TYPES
    }


def overview_context(period: str) -> dict:
    parts_qs = apply_created_range(Request.objects.all(), 'created_at', period)
    service_qs = apply_created_range(ServiceRequest.objects.all(), 'created_at', period)
    offers_qs = apply_created_range(
        RequestDispatch.objects.filter(status=RequestDispatch.STATUS_SENT),
        'sent_at',
        period,
    )

    event_counts = _event_counts(period)
    kpis = [
        KpiItem(
            'parts_created',
            parts_qs.count(),
            'Заявки на запчасти',
            'Сколько заявок покупателей создано за период',
        ),
        KpiItem(
            'services_created',
            service_qs.count(),
            'Заявки СТО',
            'Сколько заявок на услуги СТО создано за период',
        ),
        KpiItem(
            'offers_sent',
            offers_qs.count(),
            'Отправлено предложений продавцам',
            'Сколько WhatsApp-отправленных слотов RequestDispatch за период',
        ),
        KpiItem(
            'page_open',
            event_counts['page_open'],
            'Открыто страниц заявок',
            'События page_open у продавцов',
        ),
        KpiItem(
            'whatsapp_click',
            event_counts['whatsapp_click'],
            'Переходы в WhatsApp',
            'События whatsapp_click',
        ),
        KpiItem(
            'call_click',
            event_counts['call_click'],
            'Звонки',
            'События call_click',
        ),
        KpiItem(
            'out_of_stock',
            event_counts['out_of_stock'],
            'Нет в наличии',
            'Ответы продавцов «Нет в наличии»',
        ),
        KpiItem(
            'cannot_fulfill',
            event_counts['cannot_fulfill'],
            'Не могу выполнить заявку',
            'Ответы продавцов «Не могу выполнить заявку»',
        ),
        KpiItem(
            'consent_yes',
            event_counts['marketing_consent_yes'],
            'Новые согласия на предложения',
            'Выборы «Да, получать» на странице заявки',
        ),
        KpiItem(
            'consent_no',
            event_counts['marketing_consent_no'],
            'Отказы от предложений',
            'Выборы «Нет, только заявки» на странице заявки',
        ),
    ]

    recent_parts = [
        RecentRequestRow(
            pk=item.pk,
            created_at=item.created_at,
            title=vehicle_label(item.brand, item.model),
            city=dash(item.city),
            status=request_status_label(item.status),
            url_name='control_panel:parts_request_detail',
        )
        for item in apply_created_range(Request.objects.all(), 'created_at', period).order_by(
            '-created_at', '-id'
        )[:8]
    ]
    recent_services = [
        RecentRequestRow(
            pk=item.pk,
            created_at=item.created_at,
            title=dash(', '.join(svc.name for svc in list(item.services.all())[:3])),
            city=dash(item.city),
            status='Нет статуса заявки',
            url_name='control_panel:service_request_detail',
        )
        for item in apply_created_range(
            ServiceRequest.objects.prefetch_related('services'),
            'created_at',
            period,
        ).order_by('-created_at', '-id')[:8]
    ]
    recent_events = [
        RecentEventRow(
            created_at=item.created_at,
            seller_name=dash(item.seller.name if item.seller_id else ''),
            event_label=event_label(item.event_type),
            request_id=item.request_id,
        )
        for item in apply_created_range(
            SellerRequestPageEvent.objects.select_related('seller'),
            'created_at',
            period,
        ).order_by('-created_at', '-id')[:12]
    ]

    opened_exists = SellerRequestPageEvent.objects.filter(
        request_id=OuterRef('pk'),
        event_type='page_open',
    )
    contact_exists = SellerRequestPageEvent.objects.filter(
        request_id=OuterRef('pk'),
        event_type__in=['whatsapp_click', 'call_click'],
    )
    sent_unopened = list(
        Request.objects.filter(status='sent')
        .annotate(has_open=Exists(opened_exists))
        .filter(has_open=False)
        .order_by('-created_at', '-id')[:8]
    )
    opened_no_contact = list(
        Request.objects.annotate(
            has_open=Exists(opened_exists),
            has_contact=Exists(contact_exists),
        )
        .filter(has_open=True, has_contact=False)
        .order_by('-created_at', '-id')[:8]
    )
    out_of_stock_requests = list(
        Request.objects.filter(
            seller_page_events__event_type='out_of_stock',
        )
        .distinct()
        .order_by('-created_at', '-id')[:8]
    )
    active_sto = ServiceSeller.objects.filter(
        is_active=True,
        receive_requests=True,
        is_paused=False,
    ).count()

    attention: list[AttentionItem] = []
    for item in sent_unopened:
        attention.append(
            AttentionItem(
                title=f'Заявка №{item.pk} отправлена, но не открыта',
                detail=vehicle_label(item.brand, item.model),
                url_name='control_panel:parts_request_detail',
                object_id=item.pk,
            )
        )
    for item in opened_no_contact:
        attention.append(
            AttentionItem(
                title=f'Заявку №{item.pk} открыли, но нет контакта',
                detail=vehicle_label(item.brand, item.model),
                url_name='control_panel:parts_request_detail',
                object_id=item.pk,
            )
        )
    for item in out_of_stock_requests:
        attention.append(
            AttentionItem(
                title=f'По заявке №{item.pk} ответили «Нет в наличии»',
                detail=vehicle_label(item.brand, item.model),
                url_name='control_panel:parts_request_detail',
                object_id=item.pk,
            )
        )
    if active_sto == 0:
        attention.append(
            AttentionItem(
                title='Нет активных исполнителей СТО',
                detail='Нет СТО с is_active, receive_requests и без паузы',
                url_name='control_panel:sto_list',
            )
        )

    return {
        'kpis': kpis,
        'recent_parts': recent_parts,
        'recent_services': recent_services,
        'recent_events': recent_events,
        'attention_items': attention[:16],
    }
