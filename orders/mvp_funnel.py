from __future__ import annotations

from datetime import datetime, time, timedelta

from django.db.models import Count
from django.utils import timezone

from core.models import (
    Request,
    RequestDispatch,
    SellerRequestPageEvent,
    SELLER_REQUEST_PAGE_EVENT_CALL_CLICK,
    SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL,
    SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK,
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
)
from orders.models import Order, WholesaleFunnelEvent
from orders.wholesale_analytics import conversion_pct


def _aware_range(date_from, date_to):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, time.min), tz)
    end = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), time.min),
        tz,
    )
    return start, end


def build_mvp_funnel_report(date_from, date_to):
    start, end = _aware_range(date_from, date_to)

    events = WholesaleFunnelEvent.objects.filter(
        created_at__gte=start,
        created_at__lt=end,
    )
    product_viewers = events.filter(
        event_type=WholesaleFunnelEvent.EVENT_PRODUCT_VIEW,
    ).values('visitor_id').distinct().count()
    whatsapp_clickers = events.filter(
        event_type=WholesaleFunnelEvent.EVENT_SELLER_WHATSAPP_CLICK,
    ).values('visitor_id').distinct().count()
    whatsapp_clicks = events.filter(
        event_type=WholesaleFunnelEvent.EVENT_SELLER_WHATSAPP_CLICK,
    ).count()

    requests = Request.objects.filter(
        created_at__gte=start,
        created_at__lt=end,
    )
    request_count = requests.count()
    request_ids = requests.values_list('pk', flat=True)

    sent_request_count = (
        RequestDispatch.objects
        .filter(
            request_id__in=request_ids,
            status=RequestDispatch.STATUS_SENT,
        )
        .values('request_id')
        .distinct()
        .count()
    )

    page_events = SellerRequestPageEvent.objects.filter(
        request_id__in=request_ids,
        created_at__gte=start,
        created_at__lt=end,
    )
    contacted_request_count = (
        page_events
        .filter(
            event_type__in=(
                SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
                SELLER_REQUEST_PAGE_EVENT_CALL_CLICK,
            ),
        )
        .values('request_id')
        .distinct()
        .count()
    )
    declined_request_count = (
        page_events
        .filter(
            event_type__in=(
                SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK,
                SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL,
            ),
        )
        .values('request_id')
        .distinct()
        .count()
    )

    orders = Order.objects.filter(
        created_at__gte=start,
        created_at__lt=end,
    )
    order_count = orders.count()
    order_statuses = list(
        orders.values('status')
        .annotate(count=Count('id'))
        .order_by('-count', 'status')
    )

    request_sources = list(
        requests.values('source')
        .annotate(count=Count('id'))
        .order_by('-count', 'source')
    )

    return {
        'product_viewers': product_viewers,
        'whatsapp_clickers': whatsapp_clickers,
        'whatsapp_clicks': whatsapp_clicks,
        'product_to_whatsapp': conversion_pct(whatsapp_clickers, product_viewers),
        'requests': request_count,
        'requests_sent': sent_request_count,
        'requests_with_seller_contact': contacted_request_count,
        'requests_declined': declined_request_count,
        'request_to_contact': conversion_pct(contacted_request_count, sent_request_count),
        'orders': order_count,
        'order_statuses': order_statuses,
        'request_sources': request_sources,
    }
