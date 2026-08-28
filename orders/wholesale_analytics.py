"""Server-side wholesale funnel tracking. Must not affect checkout or orders."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timedelta

from django.db import DatabaseError, IntegrityError
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from .attribution import empty_utm_snapshot, get_utm_snapshot, sanitize_utm_value
from .constants import (
    SESSION_WHOLESALE_VISITOR_KEY,
    UTM_CAMPAIGN_MAX_LENGTH,
    UTM_MEDIUM_MAX_LENGTH,
    UTM_SOURCE_MAX_LENGTH,
)
from .models import Order, OrderItem, WholesaleFunnelEvent

logger = logging.getLogger(__name__)

EVENT_STOREFRONT_VIEW = WholesaleFunnelEvent.EVENT_STOREFRONT_VIEW
EVENT_PRODUCT_VIEW = WholesaleFunnelEvent.EVENT_PRODUCT_VIEW
EVENT_PRICE_DOWNLOAD = WholesaleFunnelEvent.EVENT_PRICE_DOWNLOAD
EVENT_ADD_TO_CART = WholesaleFunnelEvent.EVENT_ADD_TO_CART
EVENT_CHECKOUT_VIEW = WholesaleFunnelEvent.EVENT_CHECKOUT_VIEW
EVENT_ORDER_CREATED = WholesaleFunnelEvent.EVENT_ORDER_CREATED

DIRECT_TRAFFIC_LABEL = 'Без метки / прямой переход'

_PII_METADATA_KEYS = frozenset({
    'ip',
    'ip_address',
    'remote_addr',
    'user_agent',
    'ua',
    'http_user_agent',
    'phone',
    'customer_phone',
    'email',
    'whatsapp',
    'customer_name',
    'full_name',
    'fio',
    'address',
    'delivery_address',
    'items',
    'order_items',
    'cart_items',
})

_PII_KEY_FRAGMENTS = (
    'phone',
    'email',
    'whatsapp',
    'user_agent',
    'ip_address',
    'address',
)


def _is_staff_request(request):
    user = getattr(request, 'user', None)
    return bool(user is not None and getattr(user, 'is_staff', False))


def get_wholesale_visitor_id(request):
    """Opaque UUID stored in session. Never uses Django session_key as the ID."""
    raw = request.session.get(SESSION_WHOLESALE_VISITOR_KEY)
    try:
        visitor_id = uuid.UUID(str(raw))
    except (TypeError, ValueError, AttributeError):
        visitor_id = uuid.uuid4()
        request.session[SESSION_WHOLESALE_VISITOR_KEY] = str(visitor_id)
        request.session.modified = True
    return visitor_id


def _safe_metadata(metadata):
    if not metadata or not isinstance(metadata, dict):
        return {}
    cleaned = {}
    for key, value in metadata.items():
        key_text = str(key)
        key_l = key_text.lower()
        if key_l in _PII_METADATA_KEYS:
            continue
        if any(fragment in key_l for fragment in _PII_KEY_FRAGMENTS):
            continue
        if not isinstance(value, (str, int, float, bool, type(None))):
            continue
        cleaned[key_text[:50]] = value
    return cleaned


def _utm_from_mapping(mapping):
    snapshot = empty_utm_snapshot()
    if not mapping:
        return snapshot
    snapshot['utm_source'] = sanitize_utm_value(
        mapping.get('utm_source', ''),
        UTM_SOURCE_MAX_LENGTH,
    )
    snapshot['utm_medium'] = sanitize_utm_value(
        mapping.get('utm_medium', ''),
        UTM_MEDIUM_MAX_LENGTH,
    )
    snapshot['utm_campaign'] = sanitize_utm_value(
        mapping.get('utm_campaign', ''),
        UTM_CAMPAIGN_MAX_LENGTH,
    )
    return snapshot


def _utm_from_order(order):
    return _utm_from_mapping({
        'utm_source': getattr(order, 'utm_source', ''),
        'utm_medium': getattr(order, 'utm_medium', ''),
        'utm_campaign': getattr(order, 'utm_campaign', ''),
    })


def track_wholesale_event(
    request,
    event_type,
    seller,
    product=None,
    order=None,
    quantity=None,
    value_kzt=None,
    utm_snapshot=None,
    metadata=None,
):
    """Record a funnel event. Failures are logged and never raised to callers."""
    try:
        if request is None or seller is None or seller.pk is None:
            return None
        if _is_staff_request(request):
            return None
        if event_type not in dict(WholesaleFunnelEvent.EVENT_CHOICES):
            logger.warning('Unknown wholesale funnel event_type=%s', event_type)
            return None
        if event_type == EVENT_ORDER_CREATED:
            utm = _utm_from_order(order) if order is not None else empty_utm_snapshot()
        elif utm_snapshot is not None:
            utm = _utm_from_mapping(utm_snapshot)
        else:
            utm = get_utm_snapshot(request)

        visitor_id = get_wholesale_visitor_id(request)

        if event_type == EVENT_ORDER_CREATED and order is not None:
            if WholesaleFunnelEvent.objects.filter(
                event_type=EVENT_ORDER_CREATED,
                order_id=order.pk,
            ).exists():
                return None

        return WholesaleFunnelEvent.objects.create(
            event_type=event_type,
            seller_profile_id=seller.pk,
            product_id=getattr(product, 'pk', None) if product is not None else None,
            order_id=getattr(order, 'pk', None) if order is not None else None,
            visitor_id=visitor_id,
            utm_source=utm['utm_source'],
            utm_medium=utm['utm_medium'],
            utm_campaign=utm['utm_campaign'],
            quantity=quantity,
            value_kzt=value_kzt,
            metadata=_safe_metadata(metadata),
        )
    except (DatabaseError, IntegrityError):
        logger.warning(
            'Wholesale analytics tracking failed event_type=%s',
            event_type,
            exc_info=True,
        )
        return None
    except Exception:
        logger.warning(
            'Wholesale analytics tracking failed event_type=%s',
            event_type,
            exc_info=True,
        )
        return None


def conversion_pct(numerator, denominator):
    if not denominator:
        return None
    return (100.0 * int(numerator or 0)) / int(denominator)


def format_conversion(value):
    if value is None:
        return '—'
    if abs(value - round(value)) < 0.05:
        return f'{int(round(value))}%'
    return f'{value:.1f}%'


def utm_display(value):
    text = str(value or '').strip()
    return text or DIRECT_TRAFFIC_LABEL


def default_report_dates():
    today = timezone.localdate()
    return today - timedelta(days=29), today


def parse_report_date(value, fallback):
    parsed = parse_date(str(value or '').strip())
    return parsed or fallback


def _aware_range(date_from, date_to):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, time.min), tz)
    end = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), time.min),
        tz,
    )
    return start, end


def _filtered_events(
    date_from,
    date_to,
    seller_id=None,
    utm_source='',
    utm_medium='',
    utm_campaign='',
):
    start, end = _aware_range(date_from, date_to)
    qs = WholesaleFunnelEvent.objects.filter(
        created_at__gte=start,
        created_at__lt=end,
    )
    if seller_id:
        qs = qs.filter(seller_profile_id=seller_id)
    if utm_source:
        qs = qs.filter(utm_source=utm_source)
    if utm_medium:
        qs = qs.filter(utm_medium=utm_medium)
    if utm_campaign:
        qs = qs.filter(utm_campaign=utm_campaign)
    return qs


def _funnel_counts(qs):
    return qs.aggregate(
        storefront=Count(
            'visitor_id',
            filter=Q(event_type=EVENT_STOREFRONT_VIEW),
            distinct=True,
        ),
        product=Count(
            'visitor_id',
            filter=Q(event_type=EVENT_PRODUCT_VIEW),
            distinct=True,
        ),
        cart=Count(
            'visitor_id',
            filter=Q(event_type=EVENT_ADD_TO_CART),
            distinct=True,
        ),
        checkout=Count(
            'visitor_id',
            filter=Q(event_type=EVENT_CHECKOUT_VIEW),
            distinct=True,
        ),
        price_download=Count(
            'visitor_id',
            filter=Q(event_type=EVENT_PRICE_DOWNLOAD),
            distinct=True,
        ),
        orders=Count(
            'order_id',
            filter=Q(event_type=EVENT_ORDER_CREATED),
            distinct=True,
        ),
        raw_storefront=Count('id', filter=Q(event_type=EVENT_STOREFRONT_VIEW)),
        raw_product=Count('id', filter=Q(event_type=EVENT_PRODUCT_VIEW)),
        raw_cart=Count('id', filter=Q(event_type=EVENT_ADD_TO_CART)),
        raw_checkout=Count('id', filter=Q(event_type=EVENT_CHECKOUT_VIEW)),
        raw_price_download=Count('id', filter=Q(event_type=EVENT_PRICE_DOWNLOAD)),
        raw_orders=Count('id', filter=Q(event_type=EVENT_ORDER_CREATED)),
    )


def _order_snapshot_stats(event_qs):
    order_ids = (
        event_qs.filter(
            event_type=EVENT_ORDER_CREATED,
            order_id__isnull=False,
        ).values('order_id')
    )
    orders = Order.objects.filter(
        pk__in=order_ids,
        order_type=Order.ORDER_TYPE_WHOLESALE,
    )
    totals = orders.aggregate(
        order_count=Count('id'),
        revenue=Sum('total_price'),
    )
    items = OrderItem.objects.filter(order__in=orders).aggregate(
        quantity=Sum('quantity'),
    )
    return {
        'order_count': int(totals.get('order_count') or 0),
        'revenue': int(totals.get('revenue') or 0),
        'quantity': int(items.get('quantity') or 0),
    }


def build_wholesale_funnel_report(
    date_from,
    date_to,
    seller_id=None,
    utm_source='',
    utm_medium='',
    utm_campaign='',
):
    qs = _filtered_events(
        date_from,
        date_to,
        seller_id=seller_id,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
    )
    counts = _funnel_counts(qs)
    storefront = int(counts.get('storefront') or 0)
    product = int(counts.get('product') or 0)
    cart = int(counts.get('cart') or 0)
    checkout = int(counts.get('checkout') or 0)
    orders_unique = int(counts.get('orders') or 0)
    price_download = int(counts.get('price_download') or 0)
    order_stats = _order_snapshot_stats(qs)

    conversions = {
        'storefront_to_product': conversion_pct(product, storefront),
        'product_to_cart': conversion_pct(cart, product),
        'cart_to_checkout': conversion_pct(checkout, cart),
        'checkout_to_order': conversion_pct(orders_unique, checkout),
        'storefront_to_order': conversion_pct(orders_unique, storefront),
    }

    campaign_rows = []
    campaign_qs = (
        qs.values('utm_source', 'utm_medium', 'utm_campaign')
        .annotate(
            storefront=Count(
                'visitor_id',
                filter=Q(event_type=EVENT_STOREFRONT_VIEW),
                distinct=True,
            ),
            product=Count(
                'visitor_id',
                filter=Q(event_type=EVENT_PRODUCT_VIEW),
                distinct=True,
            ),
            cart=Count(
                'visitor_id',
                filter=Q(event_type=EVENT_ADD_TO_CART),
                distinct=True,
            ),
            checkout=Count(
                'visitor_id',
                filter=Q(event_type=EVENT_CHECKOUT_VIEW),
                distinct=True,
            ),
            price_download=Count(
                'visitor_id',
                filter=Q(event_type=EVENT_PRICE_DOWNLOAD),
                distinct=True,
            ),
            orders=Count(
                'order_id',
                filter=Q(event_type=EVENT_ORDER_CREATED),
                distinct=True,
            ),
            items_qty=Sum(
                'quantity',
                filter=Q(event_type=EVENT_ORDER_CREATED),
            ),
            revenue=Sum(
                'order__total_price',
                filter=Q(event_type=EVENT_ORDER_CREATED),
            ),
        )
        .order_by('-orders', '-storefront', 'utm_source', 'utm_medium', 'utm_campaign')
    )
    for row in campaign_qs:
        storefront_n = int(row.get('storefront') or 0)
        orders_n = int(row.get('orders') or 0)
        campaign_rows.append({
            'utm_source': utm_display(row.get('utm_source')),
            'utm_medium': utm_display(row.get('utm_medium')),
            'utm_campaign': utm_display(row.get('utm_campaign')),
            'storefront': storefront_n,
            'product': int(row.get('product') or 0),
            'cart': int(row.get('cart') or 0),
            'checkout': int(row.get('checkout') or 0),
            'price_download': int(row.get('price_download') or 0),
            'orders': orders_n,
            'items_qty': int(row.get('items_qty') or 0),
            'revenue': int(row.get('revenue') or 0),
            'conversion': conversion_pct(orders_n, storefront_n),
        })

    recent_orders = list(
        qs.filter(
            event_type=EVENT_ORDER_CREATED,
            order_id__isnull=False,
        )
        .select_related('order', 'seller_profile')
        .prefetch_related('order__items')
        .order_by('-created_at')[:30]
    )

    return {
        'storefront': storefront,
        'product': product,
        'cart': cart,
        'checkout': checkout,
        'orders': order_stats['order_count'] or orders_unique,
        'price_download': price_download,
        'items_qty': order_stats['quantity'],
        'revenue': order_stats['revenue'],
        'raw': {
            'storefront': int(counts.get('raw_storefront') or 0),
            'product': int(counts.get('raw_product') or 0),
            'cart': int(counts.get('raw_cart') or 0),
            'checkout': int(counts.get('raw_checkout') or 0),
            'price_download': int(counts.get('raw_price_download') or 0),
            'orders': int(counts.get('raw_orders') or 0),
        },
        'conversions': conversions,
        'campaigns': campaign_rows,
        'recent_orders': recent_orders,
    }
