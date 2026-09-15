"""Read-only Kaspi product economics: contribution and minimum allowed price.

Accounting convention (Kaspi Sales Report / KaspiSalesOperation):
- gross_amount is signed: PURCHASE > 0, RETURN < 0.
- commission_amount is signed cash effect on the merchant.
  Negative = Kaspi withheld a commission (cost).
  Positive = commission refunded (typical RETURN).
- delivery_cost is signed the same way (Kaspi Delivery only, never Rapido).
- commission_cost_total = -commission_signed_total
- delivery_cost_total = -delivery_signed_total
- Do NOT abs() each row. Refunds must net against charges.
- Use commission_amount only. Do not add card/pay/ex-VAT components
  on top: they would double-count if already included.
- NULL commission/delivery is a missing observation, not 0.
  Signed totals skip NULL. Expected rates use only observed rows.
- Fulfillment (Rapido packaging+handling) is a separate cost and is
  never used as Kaspi Delivery fallback.
- Historical fulfillment cost = purchase_qty * fulfillment_per_unit.
  A return does not automatically refund Rapido fees.
- Historical COGS = cost_price * net_qty when net_qty > 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from typing import Iterable

from django.db.models import Count, Q, Sum

from catalog.models import (
    KaspiEconomicsConfig,
    KaspiOrder,
    KaspiSalesOperation,
    Product,
    ProductKaspiEconomicsPolicy,
    ProductKaspiListing,
    SellerProfile,
)

ZERO = Decimal('0')
ONE = Decimal('1')
HUNDRED = Decimal('100')
TENGE = Decimal('1')

OP_PURCHASE = KaspiSalesOperation.OperationType.PURCHASE
OP_RETURN = KaspiSalesOperation.OperationType.RETURN
MATCH_LISTING = KaspiSalesOperation.MatchStatus.LISTING_MATCHED
MATCH_PRODUCT = KaspiSalesOperation.MatchStatus.PRODUCT_ONLY
MATCH_UNMATCHED = KaspiSalesOperation.MatchStatus.UNMATCHED
MATCH_AMBIGUOUS = KaspiSalesOperation.MatchStatus.AMBIGUOUS_PRODUCT

PRODUCT_MATCH_STATUSES = (MATCH_LISTING, MATCH_PRODUCT)


class ExpectedSource:
    LISTING_HISTORY = 'LISTING_HISTORY'
    PRODUCT_HISTORY = 'PRODUCT_HISTORY'
    SELLER_HISTORY = 'SELLER_HISTORY'
    UNAVAILABLE = 'UNAVAILABLE'


class EconomicsReason:
    READY = 'READY'
    MISSING_ECONOMICS_CONFIG = 'MISSING_ECONOMICS_CONFIG'
    INACTIVE_CONFIG = 'INACTIVE_CONFIG'
    MISSING_COST_PRICE = 'MISSING_COST_PRICE'
    NO_SALES_HISTORY = 'NO_SALES_HISTORY'
    NO_PRODUCT_COMMISSION_HISTORY = 'NO_PRODUCT_COMMISSION_HISTORY'
    NO_SELLER_COMMISSION_HISTORY = 'NO_SELLER_COMMISSION_HISTORY'
    NO_COMMISSION_HISTORY = 'NO_COMMISSION_HISTORY'
    NO_LISTING_COMMISSION_HISTORY = 'NO_LISTING_COMMISSION_HISTORY'
    NO_PRODUCT_DELIVERY_HISTORY = 'NO_PRODUCT_DELIVERY_HISTORY'
    NO_SELLER_DELIVERY_HISTORY = 'NO_SELLER_DELIVERY_HISTORY'
    NO_DELIVERY_HISTORY = 'NO_DELIVERY_HISTORY'
    NO_LISTING_DELIVERY_HISTORY = 'NO_LISTING_DELIVERY_HISTORY'
    INVALID_MARGIN_CONFIG = 'INVALID_MARGIN_CONFIG'
    NO_CURRENT_PRICE = 'NO_CURRENT_PRICE'
    MULTIPLE_LISTINGS = 'MULTIPLE_LISTINGS'
    NO_NET_SALES = 'NO_NET_SALES'
    ZERO_NET_QTY = 'ZERO_NET_QTY'
    NEGATIVE_NET_QTY = 'NEGATIVE_NET_QTY'
    BELOW_MIN_PRICE = 'BELOW_MIN_PRICE'
    MANUAL_FLOOR_ONLY = 'MANUAL_FLOOR_ONLY'
    SIGN_INCONSISTENT = 'SIGN_INCONSISTENT'


def percent_to_fraction(percent: Decimal) -> Decimal:
    return Decimal(percent) / HUNDRED


def to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(value)


def money_ceil_tenge(value: Decimal) -> Decimal:
    """Kaspi observed prices are integer tenge. Never round the floor down."""
    return Decimal(value).quantize(TENGE, rounding=ROUND_CEILING)


def cost_from_signed(signed: Decimal | None) -> Decimal | None:
    if signed is None:
        return None
    return -signed


def calculate_min_price(
    cost_stack: Decimal,
    commission_rate: Decimal,
    min_margin: Decimal,
) -> tuple[Decimal | None, str | None]:
    """P >= C / (1 - r - m), then ROUND_CEILING to 1 ₸."""
    if cost_stack < 0 or commission_rate < 0 or min_margin < 0:
        return None, EconomicsReason.INVALID_MARGIN_CONFIG
    denominator = ONE - commission_rate - min_margin
    if denominator <= 0:
        return None, EconomicsReason.INVALID_MARGIN_CONFIG
    return money_ceil_tenge(cost_stack / denominator), None


@dataclass
class ExpectedValue:
    value: Decimal | None
    source: str
    observation_count: int = 0


@dataclass
class SalesSlice:
    purchase_qty: int = 0
    return_qty: int = 0
    purchase_gross: Decimal = ZERO
    return_gross: Decimal = ZERO
    purchase_ops: int = 0
    return_ops: int = 0
    commission_signed: Decimal | None = None
    delivery_signed: Decimal | None = None
    commission_obs: int = 0
    delivery_obs: int = 0
    purchase_commission_signed: Decimal | None = None
    purchase_commission_gross: Decimal = ZERO
    purchase_commission_obs: int = 0
    purchase_delivery_signed: Decimal | None = None
    purchase_delivery_qty: int = 0
    purchase_delivery_obs: int = 0
    sign_issues: list[str] = field(default_factory=list)

    @property
    def net_qty(self) -> int:
        return self.purchase_qty - self.return_qty

    @property
    def net_gross(self) -> Decimal:
        return self.purchase_gross + self.return_gross

    @property
    def commission_cost_total(self) -> Decimal | None:
        return cost_from_signed(self.commission_signed)

    @property
    def delivery_cost_total(self) -> Decimal | None:
        return cost_from_signed(self.delivery_signed)

    @property
    def purchase_avg_unit_price(self) -> Decimal | None:
        if self.purchase_qty <= 0:
            return None
        return self.purchase_gross / Decimal(self.purchase_qty)

    @property
    def avg_selling_price(self) -> Decimal | None:
        if self.net_qty <= 0:
            return None
        return self.net_gross / Decimal(self.net_qty)

    def purchase_commission_rate(self) -> Decimal | None:
        if self.purchase_commission_obs <= 0 or self.purchase_commission_gross <= 0:
            return None
        cost = cost_from_signed(self.purchase_commission_signed)
        if cost is None:
            return None
        return cost / self.purchase_commission_gross

    def purchase_delivery_per_unit(self) -> Decimal | None:
        if self.purchase_delivery_obs <= 0 or self.purchase_delivery_qty <= 0:
            return None
        cost = cost_from_signed(self.purchase_delivery_signed)
        if cost is None:
            return None
        return cost / Decimal(self.purchase_delivery_qty)


@dataclass
class KaspiEconomicsResult:
    scope: str
    product_id: int | None
    listing_id: int | None
    seller_profile_id: int
    article: str = ''
    title: str = ''
    master_sku: str = ''
    merchant_sku: str = ''
    listing_count: int = 0
    product_only_ops: int = 0
    purchase_qty: int = 0
    return_qty: int = 0
    net_qty: int = 0
    purchase_gross: Decimal = ZERO
    return_gross: Decimal = ZERO
    net_gross: Decimal = ZERO
    commission_signed_total: Decimal | None = None
    commission_cost_total: Decimal | None = None
    delivery_signed_total: Decimal | None = None
    delivery_cost_total: Decimal | None = None
    avg_selling_price: Decimal | None = None
    purchase_avg_unit_price: Decimal | None = None
    expected_commission_rate: Decimal | None = None
    expected_commission_source: str = ExpectedSource.UNAVAILABLE
    expected_delivery_per_unit: Decimal | None = None
    expected_delivery_source: str = ExpectedSource.UNAVAILABLE
    fulfillment_per_unit: Decimal | None = None
    estimated_fulfillment_total: Decimal | None = None
    cost_price: Decimal | None = None
    estimated_cogs: Decimal | None = None
    contribution_profit: Decimal | None = None
    contribution_margin: Decimal | None = None
    min_margin_percent: Decimal | None = None
    calculated_min_price: Decimal | None = None
    manual_min_price: Decimal | None = None
    effective_min_price: Decimal | None = None
    current_price: Decimal | None = None
    last_known_kaspi_qty: int | None = None
    price_headroom: Decimal | None = None
    headroom_percent: Decimal | None = None
    is_historical_profit_ready: bool = False
    is_calculated_floor_ready: bool = False
    is_floor_ready: bool = False
    is_price_comparison_ready: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def primary_status(self) -> str:
        if EconomicsReason.BELOW_MIN_PRICE in self.reasons:
            return EconomicsReason.BELOW_MIN_PRICE
        if self.is_calculated_floor_ready:
            return EconomicsReason.READY
        if EconomicsReason.MANUAL_FLOOR_ONLY in self.reasons:
            return EconomicsReason.MANUAL_FLOOR_ONLY
        return self.reasons[0] if self.reasons else EconomicsReason.READY


@dataclass
class SellerEconomicsResult:
    seller_profile_id: int
    seller_name: str
    orders: int = 0
    operations: int = 0
    purchase_ops: int = 0
    return_ops: int = 0
    purchase_qty: int = 0
    return_qty: int = 0
    net_qty: int = 0
    purchase_gross: Decimal = ZERO
    return_gross: Decimal = ZERO
    net_gross: Decimal = ZERO
    commission_signed_total: Decimal | None = None
    commission_cost_total: Decimal | None = None
    delivery_signed_total: Decimal | None = None
    delivery_cost_total: Decimal | None = None
    effective_purchase_commission_rate: Decimal | None = None
    avg_purchase_delivery_per_unit: Decimal | None = None
    listing_matched: int = 0
    product_only: int = 0
    unmatched: int = 0
    ambiguous: int = 0
    unmatched_gross: Decimal = ZERO
    config: KaspiEconomicsConfig | None = None


def _add_signed(current: Decimal | None, incoming: Decimal | None, obs: int) -> Decimal | None:
    if obs <= 0 or incoming is None:
        return current
    if current is None:
        return incoming
    return current + incoming


def _merge_type_row(slice_: SalesSlice, row: dict) -> None:
    op_type = row['operation_type']
    qty = int(row['qty'] or 0)
    gross = to_decimal(row['gross']) or ZERO
    commission_obs = int(row['commission_obs'] or 0)
    delivery_obs = int(row['delivery_obs'] or 0)
    commission_signed = to_decimal(row['commission_signed']) if commission_obs else None
    delivery_signed = to_decimal(row['delivery_signed']) if delivery_obs else None
    if op_type == OP_PURCHASE:
        slice_.purchase_qty += qty
        slice_.purchase_gross += gross
        slice_.purchase_ops += int(row.get('ops') or 0)
        if gross < 0:
            slice_.sign_issues.append(EconomicsReason.SIGN_INCONSISTENT)
        slice_.purchase_commission_obs += commission_obs
        slice_.purchase_commission_signed = _add_signed(
            slice_.purchase_commission_signed, commission_signed, commission_obs
        )
        slice_.purchase_commission_gross += to_decimal(row.get('commission_gross')) or ZERO
        slice_.purchase_delivery_obs += delivery_obs
        slice_.purchase_delivery_signed = _add_signed(
            slice_.purchase_delivery_signed, delivery_signed, delivery_obs
        )
        slice_.purchase_delivery_qty += int(row.get('delivery_qty') or 0)
    elif op_type == OP_RETURN:
        slice_.return_qty += qty
        slice_.return_gross += gross
        slice_.return_ops += int(row.get('ops') or 0)
        if gross > 0:
            slice_.sign_issues.append(EconomicsReason.SIGN_INCONSISTENT)
    slice_.commission_obs += commission_obs
    slice_.delivery_obs += delivery_obs
    slice_.commission_signed = _add_signed(
        slice_.commission_signed, commission_signed, commission_obs
    )
    slice_.delivery_signed = _add_signed(
        slice_.delivery_signed, delivery_signed, delivery_obs
    )


def _operations_qs(
    seller: SellerProfile,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    qs = KaspiSalesOperation.objects.filter(seller_profile=seller)
    if date_from is not None:
        qs = qs.filter(operation_at__gte=date_from)
    if date_to is not None:
        qs = qs.filter(operation_at__lte=date_to)
    return qs


def _annotate_groups(qs, *group_fields):
    return list(
        qs.values(*group_fields, 'operation_type').annotate(
            qty=Sum('quantity'),
            gross=Sum('gross_amount'),
            ops=Count('id'),
            commission_signed=Sum('commission_amount'),
            commission_obs=Count('commission_amount'),
            commission_gross=Sum(
                'gross_amount',
                filter=Q(commission_amount__isnull=False),
            ),
            delivery_signed=Sum('delivery_cost'),
            delivery_obs=Count('delivery_cost'),
            delivery_qty=Sum(
                'quantity',
                filter=Q(delivery_cost__isnull=False),
            ),
        )
    )


def _slice_from_rows(rows: Iterable[dict]) -> SalesSlice:
    slice_ = SalesSlice()
    for row in rows:
        _merge_type_row(slice_, row)
    return slice_


def _expected_from_slices(*candidates: tuple[SalesSlice | None, str]) -> tuple[ExpectedValue, ExpectedValue]:
    commission = ExpectedValue(None, ExpectedSource.UNAVAILABLE)
    delivery = ExpectedValue(None, ExpectedSource.UNAVAILABLE)
    for slice_, source in candidates:
        if slice_ is None:
            continue
        if commission.value is None:
            rate = slice_.purchase_commission_rate()
            if rate is not None and rate >= 0:
                commission = ExpectedValue(
                    rate, source, slice_.purchase_commission_obs
                )
        if delivery.value is None:
            per_unit = slice_.purchase_delivery_per_unit()
            if per_unit is not None and per_unit >= 0:
                delivery = ExpectedValue(
                    per_unit, source, slice_.purchase_delivery_obs
                )
    return commission, delivery


def _build_result(
    *,
    scope: str,
    seller: SellerProfile,
    config: KaspiEconomicsConfig | None,
    slice_: SalesSlice,
    product: Product | None = None,
    listing: ProductKaspiListing | None = None,
    listings: list[ProductKaspiListing] | None = None,
    policy: ProductKaspiEconomicsPolicy | None = None,
    product_only_ops: int = 0,
    expected_commission: ExpectedValue,
    expected_delivery: ExpectedValue,
    product_slice: SalesSlice | None = None,
    seller_slice: SalesSlice | None = None,
) -> KaspiEconomicsResult:
    listings = listings or []
    reasons: list[str] = []
    result = KaspiEconomicsResult(
        scope=scope,
        product_id=product.pk if product else None,
        listing_id=listing.pk if listing else None,
        seller_profile_id=seller.pk,
        article=(product.article if product else ''),
        title=(product.title if product else ''),
        master_sku=(listing.master_sku if listing else ''),
        merchant_sku=(listing.merchant_sku if listing else ''),
        listing_count=len(listings),
        product_only_ops=product_only_ops,
        purchase_qty=slice_.purchase_qty,
        return_qty=slice_.return_qty,
        net_qty=slice_.net_qty,
        purchase_gross=slice_.purchase_gross,
        return_gross=slice_.return_gross,
        net_gross=slice_.net_gross,
        commission_signed_total=slice_.commission_signed,
        commission_cost_total=slice_.commission_cost_total,
        delivery_signed_total=slice_.delivery_signed,
        delivery_cost_total=slice_.delivery_cost_total,
        avg_selling_price=slice_.avg_selling_price,
        purchase_avg_unit_price=slice_.purchase_avg_unit_price,
        expected_commission_rate=expected_commission.value,
        expected_commission_source=expected_commission.source,
        expected_delivery_per_unit=expected_delivery.value,
        expected_delivery_source=expected_delivery.source,
    )
    if slice_.sign_issues:
        reasons.append(EconomicsReason.SIGN_INCONSISTENT)
    if config is None:
        reasons.append(EconomicsReason.MISSING_ECONOMICS_CONFIG)
    elif not config.is_active:
        reasons.append(EconomicsReason.INACTIVE_CONFIG)
    else:
        result.fulfillment_per_unit = config.fulfillment_total_per_unit
        result.estimated_fulfillment_total = (
            config.fulfillment_total_per_unit * Decimal(slice_.purchase_qty)
        )

    cost_price = to_decimal(product.cost_price) if product else None
    result.cost_price = cost_price
    if product is not None and cost_price is None:
        reasons.append(EconomicsReason.MISSING_COST_PRICE)

    if slice_.purchase_ops + slice_.return_ops == 0 and slice_.purchase_qty == 0:
        reasons.append(EconomicsReason.NO_SALES_HISTORY)

    if slice_.net_qty == 0 and slice_.purchase_qty > 0:
        reasons.append(EconomicsReason.ZERO_NET_QTY)
        reasons.append(EconomicsReason.NO_NET_SALES)
    elif slice_.net_qty < 0:
        reasons.append(EconomicsReason.NEGATIVE_NET_QTY)
        reasons.append(EconomicsReason.NO_NET_SALES)

    if expected_commission.source == ExpectedSource.UNAVAILABLE:
        if product_slice is not None and product_slice.purchase_commission_obs == 0:
            reasons.append(EconomicsReason.NO_PRODUCT_COMMISSION_HISTORY)
        if seller_slice is not None and seller_slice.purchase_commission_obs == 0:
            reasons.append(EconomicsReason.NO_SELLER_COMMISSION_HISTORY)
        reasons.append(EconomicsReason.NO_COMMISSION_HISTORY)
        if scope == 'listing':
            reasons.append(EconomicsReason.NO_LISTING_COMMISSION_HISTORY)
    if expected_delivery.source == ExpectedSource.UNAVAILABLE:
        if product_slice is not None and product_slice.purchase_delivery_obs == 0:
            reasons.append(EconomicsReason.NO_PRODUCT_DELIVERY_HISTORY)
        if seller_slice is not None and seller_slice.purchase_delivery_obs == 0:
            reasons.append(EconomicsReason.NO_SELLER_DELIVERY_HISTORY)
        reasons.append(EconomicsReason.NO_DELIVERY_HISTORY)
        if scope == 'listing':
            reasons.append(EconomicsReason.NO_LISTING_DELIVERY_HISTORY)

    min_margin_percent = None
    if policy is not None and policy.min_margin_percent is not None:
        min_margin_percent = policy.min_margin_percent
    elif config is not None:
        min_margin_percent = config.default_min_margin_percent
    result.min_margin_percent = min_margin_percent
    result.manual_min_price = (
        to_decimal(policy.manual_min_price) if policy is not None else None
    )

    if (
        cost_price is not None
        and slice_.net_qty > 0
        and result.estimated_fulfillment_total is not None
        and slice_.commission_cost_total is not None
        and slice_.delivery_cost_total is not None
        and config is not None
        and config.is_active
    ):
        result.estimated_cogs = cost_price * Decimal(slice_.net_qty)
        result.contribution_profit = (
            slice_.net_gross
            - result.estimated_cogs
            - slice_.commission_cost_total
            - slice_.delivery_cost_total
            - result.estimated_fulfillment_total
        )
        if slice_.net_gross > 0:
            result.contribution_margin = (
                result.contribution_profit / slice_.net_gross
            )
        result.is_historical_profit_ready = True

    calculated = None
    calc_error = None
    if (
        cost_price is not None
        and result.fulfillment_per_unit is not None
        and expected_commission.value is not None
        and expected_delivery.value is not None
        and min_margin_percent is not None
        and config is not None
        and config.is_active
    ):
        cost_stack = cost_price + result.fulfillment_per_unit + expected_delivery.value
        calculated, calc_error = calculate_min_price(
            cost_stack,
            expected_commission.value,
            percent_to_fraction(min_margin_percent),
        )
        if calc_error:
            reasons.append(calc_error)
    result.calculated_min_price = calculated
    result.is_calculated_floor_ready = calculated is not None
    if calculated is not None:
        if result.manual_min_price is not None:
            result.effective_min_price = max(calculated, result.manual_min_price)
        else:
            result.effective_min_price = calculated
        result.is_floor_ready = True
    elif result.manual_min_price is not None:
        result.effective_min_price = result.manual_min_price
        result.is_floor_ready = True
        reasons.append(EconomicsReason.MANUAL_FLOOR_ONLY)

    if listing is not None:
        result.current_price = to_decimal(listing.last_known_our_price)
        result.last_known_kaspi_qty = listing.last_known_kaspi_qty
        if result.current_price is None:
            reasons.append(EconomicsReason.NO_CURRENT_PRICE)
    elif product is not None:
        priced = [
            to_decimal(item.last_known_our_price)
            for item in listings
            if item.last_known_our_price is not None
        ]
        if len(listings) > 1:
            reasons.append(EconomicsReason.MULTIPLE_LISTINGS)
            result.current_price = None
        elif len(priced) == 1:
            result.current_price = priced[0]
        else:
            reasons.append(EconomicsReason.NO_CURRENT_PRICE)

    if result.current_price is not None and result.effective_min_price is not None:
        result.price_headroom = result.current_price - result.effective_min_price
        if result.current_price > 0:
            result.headroom_percent = result.price_headroom / result.current_price
        result.is_price_comparison_ready = True
        if result.price_headroom < 0:
            reasons.append(EconomicsReason.BELOW_MIN_PRICE)

    # Deduplicate reasons, keep order.
    seen = set()
    ordered = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            ordered.append(reason)
    if result.is_calculated_floor_ready and EconomicsReason.READY not in ordered:
        ordered.append(EconomicsReason.READY)
    result.reasons = ordered
    return result


def seller_economics(
    seller: SellerProfile,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> SellerEconomicsResult:
    config = KaspiEconomicsConfig.objects.filter(seller_profile=seller).first()
    qs = _operations_qs(seller, date_from=date_from, date_to=date_to)
    slice_ = _slice_from_rows(_annotate_groups(qs))
    match_counts = dict(
        qs.values_list('match_status').annotate(n=Count('id')).values_list(
            'match_status', 'n'
        )
    )
    unmatched_gross = qs.filter(match_status=MATCH_UNMATCHED).aggregate(
        total=Sum('gross_amount')
    )['total'] or ZERO
    return SellerEconomicsResult(
        seller_profile_id=seller.pk,
        seller_name=seller.name,
        orders=_seller_order_count(seller, qs, date_from, date_to),
        operations=qs.count(),
        purchase_ops=slice_.purchase_ops,
        return_ops=slice_.return_ops,
        purchase_qty=slice_.purchase_qty,
        return_qty=slice_.return_qty,
        net_qty=slice_.net_qty,
        purchase_gross=slice_.purchase_gross,
        return_gross=slice_.return_gross,
        net_gross=slice_.net_gross,
        commission_signed_total=slice_.commission_signed,
        commission_cost_total=slice_.commission_cost_total,
        delivery_signed_total=slice_.delivery_signed,
        delivery_cost_total=slice_.delivery_cost_total,
        effective_purchase_commission_rate=slice_.purchase_commission_rate(),
        avg_purchase_delivery_per_unit=slice_.purchase_delivery_per_unit(),
        listing_matched=int(match_counts.get(MATCH_LISTING, 0)),
        product_only=int(match_counts.get(MATCH_PRODUCT, 0)),
        unmatched=int(match_counts.get(MATCH_UNMATCHED, 0)),
        ambiguous=int(match_counts.get(MATCH_AMBIGUOUS, 0)),
        unmatched_gross=unmatched_gross,
        config=config,
    )


def _seller_order_count(seller, qs, date_from, date_to) -> int:
    if date_from is None and date_to is None:
        return KaspiOrder.objects.filter(seller_profile=seller).count()
    return qs.values('order_id').distinct().count()


def load_economics_context(
    seller: SellerProfile,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    product_ids: Iterable[int] | None = None,
):
    config = KaspiEconomicsConfig.objects.filter(seller_profile=seller).first()
    products = Product.objects.filter(seller_profile=seller).prefetch_related(
        'kaspi_listings'
    )
    if product_ids is not None:
        products = products.filter(pk__in=list(product_ids))
    product_map = {product.pk: product for product in products}
    listings_by_product: dict[int, list[ProductKaspiListing]] = {}
    listing_map: dict[int, ProductKaspiListing] = {}
    for product in product_map.values():
        items = list(product.kaspi_listings.all())
        listings_by_product[product.pk] = items
        for listing in items:
            listing_map[listing.pk] = listing
    policies = {
        policy.product_id: policy
        for policy in ProductKaspiEconomicsPolicy.objects.filter(
            product_id__in=product_map
        )
    }

    qs = _operations_qs(seller, date_from=date_from, date_to=date_to)
    seller_slice = _slice_from_rows(_annotate_groups(qs))
    product_rows = _annotate_groups(
        qs.filter(
            match_status__in=PRODUCT_MATCH_STATUSES,
            product_id__isnull=False,
        ),
        'product_id',
    )
    listing_rows = _annotate_groups(
        qs.filter(match_status=MATCH_LISTING, listing_id__isnull=False),
        'listing_id',
        'product_id',
    )
    product_slices: dict[int, SalesSlice] = {}
    for row in product_rows:
        slice_ = product_slices.setdefault(row['product_id'], SalesSlice())
        _merge_type_row(slice_, row)
    listing_slices: dict[int, SalesSlice] = {}
    listing_product: dict[int, int] = {}
    for row in listing_rows:
        slice_ = listing_slices.setdefault(row['listing_id'], SalesSlice())
        _merge_type_row(slice_, row)
        listing_product[row['listing_id']] = row['product_id']
    product_only_ops = dict(
        qs.filter(match_status=MATCH_PRODUCT, product_id__isnull=False)
        .values_list('product_id')
        .annotate(n=Count('id'))
        .values_list('product_id', 'n')
    )
    match_counts = dict(
        qs.values_list('match_status').annotate(n=Count('id')).values_list(
            'match_status', 'n'
        )
    )
    unmatched_gross = qs.filter(match_status=MATCH_UNMATCHED).aggregate(
        total=Sum('gross_amount')
    )['total'] or ZERO
    seller_result = SellerEconomicsResult(
        seller_profile_id=seller.pk,
        seller_name=seller.name,
        orders=_seller_order_count(seller, qs, date_from, date_to),
        operations=qs.count(),
        purchase_ops=seller_slice.purchase_ops,
        return_ops=seller_slice.return_ops,
        purchase_qty=seller_slice.purchase_qty,
        return_qty=seller_slice.return_qty,
        net_qty=seller_slice.net_qty,
        purchase_gross=seller_slice.purchase_gross,
        return_gross=seller_slice.return_gross,
        net_gross=seller_slice.net_gross,
        commission_signed_total=seller_slice.commission_signed,
        commission_cost_total=seller_slice.commission_cost_total,
        delivery_signed_total=seller_slice.delivery_signed,
        delivery_cost_total=seller_slice.delivery_cost_total,
        effective_purchase_commission_rate=seller_slice.purchase_commission_rate(),
        avg_purchase_delivery_per_unit=seller_slice.purchase_delivery_per_unit(),
        listing_matched=int(match_counts.get(MATCH_LISTING, 0)),
        product_only=int(match_counts.get(MATCH_PRODUCT, 0)),
        unmatched=int(match_counts.get(MATCH_UNMATCHED, 0)),
        ambiguous=int(match_counts.get(MATCH_AMBIGUOUS, 0)),
        unmatched_gross=unmatched_gross,
        config=config,
    )
    return {
        'config': config,
        'products': product_map,
        'listings_by_product': listings_by_product,
        'listing_map': listing_map,
        'policies': policies,
        'seller_slice': seller_slice,
        'seller_result': seller_result,
        'product_slices': product_slices,
        'listing_slices': listing_slices,
        'listing_product': listing_product,
        'product_only_ops': product_only_ops,
    }


def product_economics_from_context(ctx, product: Product) -> KaspiEconomicsResult:
    config = ctx['config']
    seller = product.seller_profile
    listings = ctx['listings_by_product'].get(product.pk, [])
    slice_ = ctx['product_slices'].get(product.pk, SalesSlice())
    seller_slice = ctx['seller_slice']
    commission, delivery = _expected_from_slices(
        (slice_, ExpectedSource.PRODUCT_HISTORY),
        (seller_slice, ExpectedSource.SELLER_HISTORY),
    )
    return _build_result(
        scope='product',
        seller=seller,
        config=config,
        slice_=slice_,
        product=product,
        listings=listings,
        policy=ctx['policies'].get(product.pk),
        product_only_ops=int(ctx['product_only_ops'].get(product.pk, 0)),
        expected_commission=commission,
        expected_delivery=delivery,
        product_slice=slice_,
        seller_slice=seller_slice,
    )


def listing_economics_from_context(ctx, listing: ProductKaspiListing) -> KaspiEconomicsResult:
    product = listing.product
    config = ctx['config']
    listing_slice = ctx['listing_slices'].get(listing.pk, SalesSlice())
    product_slice = ctx['product_slices'].get(product.pk, SalesSlice())
    seller_slice = ctx['seller_slice']
    commission, delivery = _expected_from_slices(
        (listing_slice, ExpectedSource.LISTING_HISTORY),
        (product_slice, ExpectedSource.PRODUCT_HISTORY),
        (seller_slice, ExpectedSource.SELLER_HISTORY),
    )
    return _build_result(
        scope='listing',
        seller=product.seller_profile,
        config=config,
        slice_=listing_slice,
        product=product,
        listing=listing,
        listings=ctx['listings_by_product'].get(product.pk, []),
        policy=ctx['policies'].get(product.pk),
        product_only_ops=int(ctx['product_only_ops'].get(product.pk, 0)),
        expected_commission=commission,
        expected_delivery=delivery,
        product_slice=product_slice,
        seller_slice=seller_slice,
    )


def report_product_economics(
    seller: SellerProfile,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    product_id: int | None = None,
    article: str | None = None,
) -> tuple[SellerEconomicsResult, list[KaspiEconomicsResult]]:
    product_ids = None
    if product_id is not None or article:
        filtered = Product.objects.filter(seller_profile=seller)
        if product_id is not None:
            filtered = filtered.filter(pk=product_id)
        if article:
            filtered = filtered.filter(article=article)
        product_ids = list(filtered.values_list('pk', flat=True))
    ctx = load_economics_context(
        seller,
        date_from=date_from,
        date_to=date_to,
        product_ids=product_ids,
    )
    results = [
        product_economics_from_context(ctx, product)
        for product in ctx['products'].values()
    ]
    results.sort(key=lambda item: (item.article, item.product_id or 0))
    return ctx['seller_result'], results


def report_listing_economics(
    seller: SellerProfile,
    listing_id: int,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> tuple[SellerEconomicsResult, KaspiEconomicsResult | None]:
    listing = (
        ProductKaspiListing.objects.filter(
            pk=listing_id,
            product__seller_profile=seller,
        )
        .select_related('product')
        .first()
    )
    product_ids = [listing.product_id] if listing else []
    ctx = load_economics_context(
        seller,
        date_from=date_from,
        date_to=date_to,
        product_ids=product_ids or None,
    )
    if listing is None:
        return ctx['seller_result'], None
    listing = ctx['listing_map'].get(listing_id) or listing
    return ctx['seller_result'], listing_economics_from_context(ctx, listing)


def product_is_problem(result: KaspiEconomicsResult) -> bool:
    if EconomicsReason.BELOW_MIN_PRICE in result.reasons:
        return True
    if not result.is_calculated_floor_ready:
        return True
    blocking = {
        EconomicsReason.MISSING_COST_PRICE,
        EconomicsReason.MISSING_ECONOMICS_CONFIG,
        EconomicsReason.INACTIVE_CONFIG,
        EconomicsReason.NO_COMMISSION_HISTORY,
        EconomicsReason.NO_DELIVERY_HISTORY,
        EconomicsReason.INVALID_MARGIN_CONFIG,
    }
    return any(reason in blocking for reason in result.reasons)
