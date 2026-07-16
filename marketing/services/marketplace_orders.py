from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Max, Q, QuerySet

from core.models import BuyerContact
from core.services.buyer_contact_utils import mask_phone
from marketing.services.phone_utils import normalize_phone_key
from orders.models import Order

MARKETPLACE_ORDER_COUNTABLE_STATUSES = (
    Order.STATUS_PAID,
)

MARKETPLACE_BUYERS_CARD_TITLE = 'Покупатели товаров маркетплейса'
MARKETPLACE_BUYERS_EMPTY_NOTE = (
    'Данные появятся после оплаченных заказов через маркетплейс'
)
MARKETPLACE_BUYERS_PAID_NOTE = (
    'Учитываются только заказы со статусом «Оплачен»'
)
MARKETPLACE_BUYERS_FILTER_NOTE = (
    'Учитываются только заказы со статусом «Оплачен» и корректным телефоном'
)
SELLER_EXECUTOR_CONSENT_NOTE = 'Рекламное согласие: не зафиксировано'


@dataclass(frozen=True)
class MarketplaceOrderAuditRow:
    order_id: int
    masked_phone: str
    status: str
    status_label: str
    created_at: datetime
    is_paid: bool
    is_cancelled: bool
    is_test_phone: bool
    included_in_real_stats: bool
    included_in_test_stats: bool
    inclusion_reason: str


@dataclass(frozen=True)
class MarketplaceBuyerCounts:
    real_total: int
    test_total: int
    real_phones: frozenset[str]
    test_phones: frozenset[str]


def get_test_marketplace_phone_keys() -> frozenset[str]:
    return frozenset(
        BuyerContact.objects.filter(is_test_contact=True).values_list(
            'phone_normalized',
            flat=True,
        ),
    )


def get_marketplace_orders_queryset() -> QuerySet[Order]:
    return Order.objects.filter(
        status__in=MARKETPLACE_ORDER_COUNTABLE_STATUSES,
    ).exclude(customer_phone='')


def collect_paid_marketplace_phone_keys() -> frozenset[str]:
    phones: set[str] = set()
    for order in get_marketplace_orders_queryset().only('customer_phone'):
        phone_key = normalize_phone_key(order.customer_phone)
        if phone_key:
            phones.add(phone_key)
    return frozenset(phones)


def get_marketplace_buyer_counts() -> MarketplaceBuyerCounts:
    paid_phones = collect_paid_marketplace_phone_keys()
    test_phone_keys = get_test_marketplace_phone_keys()
    test_paid_phones = paid_phones & test_phone_keys
    real_paid_phones = paid_phones - test_phone_keys
    return MarketplaceBuyerCounts(
        real_total=len(real_paid_phones),
        test_total=len(test_paid_phones),
        real_phones=real_paid_phones,
        test_phones=test_paid_phones,
    )


def explain_marketplace_order_inclusion(order: Order) -> tuple[bool, bool, str]:
    phone_key = normalize_phone_key(order.customer_phone)
    if not phone_key:
        return False, False, 'Некорректный или пустой телефон — не учитывается'
    if order.status == Order.STATUS_CANCELLED:
        return False, False, 'Статус «Отменён» — не учитывается'
    if order.status not in MARKETPLACE_ORDER_COUNTABLE_STATUSES:
        return (
            False,
            False,
            f'Статус «{order.get_status_display()}» — незавершённая покупка, не учитывается',
        )
    test_phone_keys = get_test_marketplace_phone_keys()
    if phone_key in test_phone_keys:
        return (
            False,
            True,
            'Учитывается как тестовый оплаченный покупатель маркетплейса',
        )
    return True, False, 'Учитывается как реальный оплаченный покупатель маркетплейса'


def audit_marketplace_orders() -> list[MarketplaceOrderAuditRow]:
    rows: list[MarketplaceOrderAuditRow] = []
    test_phone_keys = get_test_marketplace_phone_keys()
    for order in Order.objects.order_by('id'):
        phone_key = normalize_phone_key(order.customer_phone)
        included_real, included_test, reason = explain_marketplace_order_inclusion(order)
        rows.append(
            MarketplaceOrderAuditRow(
                order_id=order.pk,
                masked_phone=mask_phone(phone_key or order.customer_phone),
                status=order.status,
                status_label=order.get_status_display(),
                created_at=order.created_at,
                is_paid=order.status == Order.STATUS_PAID,
                is_cancelled=order.status == Order.STATUS_CANCELLED,
                is_test_phone=bool(phone_key and phone_key in test_phone_keys),
                included_in_real_stats=included_real,
                included_in_test_stats=included_test,
                inclusion_reason=reason,
            ),
        )
    return rows


def iter_marketplace_order_phone_stats():
    return (
        get_marketplace_orders_queryset()
        .values('customer_phone')
        .annotate(
            orders_count=Count('id'),
            last_activity=Max('created_at'),
            customer_name=Max('customer_name'),
        )
    )


def has_non_countable_marketplace_orders() -> bool:
    return Order.objects.filter(
        ~Q(status__in=MARKETPLACE_ORDER_COUNTABLE_STATUSES),
    ).exclude(
        customer_phone='',
    ).exclude(
        status=Order.STATUS_CANCELLED,
    ).exists()
