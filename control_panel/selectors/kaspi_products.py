"""Read-only Control workbench for Product × Kaspi × warehouse × sales."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import Count, Exists, F, Max, OuterRef, Prefetch, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.http import QueryDict
from django.utils import timezone

from catalog.kaspi_economics import load_economics_context, product_economics_from_context
from catalog.kaspi_public_url import display_kaspi_public_url
from catalog.models import (
    Category,
    KaspiEconomicsConfig,
    KaspiSalesOperation,
    Product,
    ProductKaspiListing,
    ProductWarehouseStock,
)
from catalog.warehouses import WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2
from control_panel.selectors.common import first_value, paginate

MATCHED = (
    KaspiSalesOperation.MatchStatus.LISTING_MATCHED,
    KaspiSalesOperation.MatchStatus.PRODUCT_ONLY,
)
OP_PURCHASE = KaspiSalesOperation.OperationType.PURCHASE
OP_RETURN = KaspiSalesOperation.OperationType.RETURN

SORT_CHOICES = (
    ('article', 'Артикул'),
    ('title', 'Название'),
    ('pp2', 'PP2'),
    ('sales_30', 'Продажи 30 дн.'),
    ('price', 'Наша цена'),
)
SORT_DEFAULT = 'article'
PAGE_FILTERS = (
    ('', 'Все'),
    ('on_pp2', 'Есть на PP2'),
    ('no_pp2', 'Нет на PP2'),
    ('has_sales', 'Есть продажи'),
    ('no_sales', 'Нет продаж 30 дней'),
    ('mismatch', 'Расхождение остатков'),
    ('attention', 'Требует внимания'),
)
CATEGORY_CHIPS = (
    ('filters', 'Фильтры (товары)', 'фильтр'),
    ('spark', 'Свечи', 'свеч'),
)


@dataclass
class ListingView:
    pk: int
    master_sku: str
    merchant_sku: str
    our_price: int | None
    our_price_label: str
    kaspi_qty: int | None
    kaspi_qty_label: str
    last_synced_at: datetime | None
    is_active: bool
    public_url: str


@dataclass
class StatusBadge:
    text: str
    tone: str


@dataclass
class ProductWorkRow:
    pk: int
    article: str
    title: str
    brand: str
    category: str
    image_url: str | None
    zpt_status: str
    zpt_tone: str
    has_kaspi_listing: bool
    listing_count: int
    listings: list[ListingView]
    our_price: int | None
    our_price_label: str
    competitor_label: str
    delta_label: str
    rule_label: str
    recommended_label: str
    pp1: int
    pp2: int
    kaspi_qty: int | None
    kaspi_qty_label: str
    stock_delta: int | None
    sales_7: int
    sales_30: int
    returns_30: int
    sales_all: int
    purchases_all: int
    returns_all: int
    net_gross_30: Decimal | None
    net_gross_30_label: str
    stock_delta_label: str
    primary_badge: StatusBadge
    extra_badge_count: int
    status_title: str
    recommended_note: str
    badges: list[StatusBadge]
    zpt_url: str
    kaspi_url: str
    public_url: str
    economics_configured: bool
    cost_price: int | None
    commission_rate: Decimal | None
    delivery_per_unit: Decimal | None
    fulfillment_per_unit: Decimal | None
    economic_floor: Decimal | None
    contribution: Decimal | None
    economics_note: str


def _stock_qty_subquery(code: str):
    return Subquery(
        ProductWarehouseStock.objects.filter(
            product_id=OuterRef('pk'),
            warehouse__code=code,
        ).values('quantity')[:1]
    )


def _single_listing_value(field: str):
    return Subquery(
        ProductKaspiListing.objects.filter(product_id=OuterRef('pk'))
        .order_by('id')
        .values(field)[:1]
    )


def _sales_sum_subquery(*, since, operation_type):
    return Subquery(
        KaspiSalesOperation.objects.filter(
            product_id=OuterRef('pk'),
            match_status__in=MATCHED,
            operation_type=operation_type,
            operation_at__gte=since,
        )
        .values('product_id')
        .annotate(total=Sum('quantity'))
        .values('total')[:1]
    )


def _matched_ops(*, since=None):
    qs = KaspiSalesOperation.objects.filter(match_status__in=MATCHED)
    if since is not None:
        qs = qs.filter(operation_at__gte=since)
    return qs


def available_category_chips() -> list[tuple[str, str]]:
    chips = []
    for key, label, token in CATEGORY_CHIPS:
        if Category.objects.filter(name__icontains=token).exists():
            chips.append((key, label))
    return chips


def _apply_search(qs, query: str):
    if not query:
        return qs
    matched_ids = Product.objects.filter(
        Q(article__icontains=query)
        | Q(title__icontains=query)
        | Q(barcodes__code__icontains=query)
        | Q(kaspi_listings__master_sku__icontains=query)
        | Q(kaspi_listings__merchant_sku__icontains=query)
        | Q(kaspi_listings__barcode__icontains=query)
    ).values('pk')
    return qs.filter(pk__in=matched_ids)


def _annotate_workbench(qs, *, since_30):
    return qs.annotate(
        listing_count=Count('kaspi_listings', distinct=True),
        pp1_qty=Coalesce(_stock_qty_subquery(WAREHOUSE_CODE_PP1), 0),
        pp2_qty=Coalesce(_stock_qty_subquery(WAREHOUSE_CODE_PP2), 0),
        kaspi_qty_one=_single_listing_value('last_known_kaspi_qty'),
        our_price_one=_single_listing_value('last_known_our_price'),
        has_kaspi_price=Exists(
            ProductKaspiListing.objects.filter(
                product_id=OuterRef('pk'),
                last_known_our_price__isnull=False,
            )
        ),
        has_sales_30=Exists(
            _matched_ops(since=since_30).filter(
                product_id=OuterRef('pk'),
                operation_type=OP_PURCHASE,
            )
        ),
        has_returns_30=Exists(
            _matched_ops(since=since_30).filter(
                product_id=OuterRef('pk'),
                operation_type=OP_RETURN,
            )
        ),
    )


def _mismatch_q():
    return Q(listing_count=1, kaspi_qty_one__isnull=False) & ~Q(
        kaspi_qty_one=F('pp2_qty')
    )


def _attention_q():
    has_listing = Q(listing_count__gt=0)
    return (
        (has_listing & Q(pp2_qty=0))
        | (has_listing & Q(has_kaspi_price=False))
        | _mismatch_q()
        | Q(has_returns_30=True)
    )


def _apply_filter(qs, key: str, category_chips: list[tuple[str, str]]):
    if key == 'on_pp2':
        return qs.filter(pp2_qty__gt=0)
    if key == 'no_pp2':
        return qs.filter(pp2_qty=0)
    if key == 'has_sales':
        return qs.filter(has_sales_30=True)
    if key == 'no_sales':
        return qs.filter(has_sales_30=False)
    if key == 'mismatch':
        return qs.filter(_mismatch_q())
    if key == 'attention':
        return qs.filter(_attention_q())
    token_by_key = {item[0]: item[2] for item in CATEGORY_CHIPS}
    allowed = {item[0] for item in category_chips}
    if key in allowed:
        return qs.filter(category__name__icontains=token_by_key[key])
    return qs


def _apply_sort(qs, sort: str, *, since_30):
    if sort == 'title':
        return qs.order_by('title', 'pk')
    if sort == 'pp2':
        return qs.order_by('-pp2_qty', 'article', 'pk')
    if sort == 'price':
        return qs.order_by(F('our_price_one').asc(nulls_last=True), 'article', 'pk')
    if sort == 'sales_30':
        sales = Coalesce(_sales_sum_subquery(since=since_30, operation_type=OP_PURCHASE), 0)
        returns = Coalesce(_sales_sum_subquery(since=since_30, operation_type=OP_RETURN), 0)
        return qs.annotate(sales_30_net=sales - returns).order_by(
            '-sales_30_net', 'article', 'pk'
        )
    return qs.order_by('article', 'pk')


def _querystring(params: QueryDict, **overrides) -> str:
    data = {}
    for key in ('q', 'filter', 'sort'):
        value = overrides[key] if key in overrides else first_value(params, key)
        if value:
            data[key] = value
    return urlencode(data)


def _stock_map(product: Product) -> dict[str, int]:
    result = {WAREHOUSE_CODE_PP1: 0, WAREHOUSE_CODE_PP2: 0}
    for row in product.warehouse_stocks.all():
        result[row.warehouse.code] = int(row.quantity)
    return result


def _sales_buckets(product_ids: list[int], *, since_7, since_30):
    empty = {
        'sales_7': 0,
        'sales_30': 0,
        'returns_30': 0,
        'sales_all': 0,
        'purchases_all': 0,
        'returns_all': 0,
        'net_gross_30': None,
    }
    buckets = {pk: dict(empty) for pk in product_ids}
    if not product_ids:
        return buckets
    rows = (
        _matched_ops()
        .filter(product_id__in=product_ids)
        .values('product_id', 'operation_type')
        .annotate(
            qty_all=Sum('quantity'),
            qty_7=Sum('quantity', filter=Q(operation_at__gte=since_7)),
            qty_30=Sum('quantity', filter=Q(operation_at__gte=since_30)),
            gross_30=Sum('gross_amount', filter=Q(operation_at__gte=since_30)),
        )
    )
    for row in rows:
        item = buckets[row['product_id']]
        op_type = row['operation_type']
        qty_all = int(row['qty_all'] or 0)
        qty_7 = int(row['qty_7'] or 0)
        qty_30 = int(row['qty_30'] or 0)
        gross_30 = row['gross_30']
        if op_type == OP_PURCHASE:
            item['purchases_all'] += qty_all
            item['sales_all'] += qty_all
            item['sales_7'] += qty_7
            item['sales_30'] += qty_30
            if gross_30 is not None:
                item['net_gross_30'] = (item['net_gross_30'] or Decimal('0')) + gross_30
        elif op_type == OP_RETURN:
            item['returns_all'] += qty_all
            item['sales_all'] -= qty_all
            item['sales_7'] -= qty_7
            item['sales_30'] -= qty_30
            item['returns_30'] += qty_30
            if gross_30 is not None:
                item['net_gross_30'] = (item['net_gross_30'] or Decimal('0')) + gross_30
    return buckets


def _economics_by_product(products: list[Product]):
    grouped: dict[int, list[Product]] = defaultdict(list)
    for product in products:
        if product.seller_profile_id:
            grouped[product.seller_profile_id].append(product)
    result = {}
    for product_list in grouped.values():
        seller = product_list[0].seller_profile
        ctx = load_economics_context(
            seller,
            product_ids=[item.pk for item in product_list],
        )
        for product in product_list:
            result[product.pk] = product_economics_from_context(ctx, product)
    return result


def _group_int(value: int) -> str:
    return f'{int(value):,}'.replace(',', ' ')


def _format_kzt(value) -> str:
    if value is None:
        return '—'
    number = Decimal(str(value))
    if number == number.to_integral_value():
        return f'{_group_int(int(number))} ₸'
    quantized = format(number, ',.2f').replace(',', ' ')
    return f'{quantized} ₸'


def _format_delta(value: int | None) -> str:
    if value is None:
        return '—'
    if value > 0:
        return f'+{value}'
    return str(value)


def _listing_price_label(listing_count: int, our_price: int | None) -> str:
    if listing_count == 0:
        return '—'
    if listing_count > 1:
        return 'Несколько'
    if our_price is None:
        return '—'
    return _format_kzt(our_price)


def _listing_qty_label(listing_count: int, kaspi_qty: int | None) -> str:
    if listing_count == 0:
        return '—'
    if listing_count > 1:
        return 'Несколько'
    if kaspi_qty is None:
        return '—'
    return str(kaspi_qty)


_BADGE_PRIORITY = (
    'Расхождение',
    'Нет PP2',
    'Нет Kaspi',
    'Нет Kaspi цены',
    'Возвраты',
    'Нет продаж',
    'Несколько listing',
    'OK',
)


def _build_badges(row: ProductWorkRow) -> list[StatusBadge]:
    badges: list[StatusBadge] = []
    if not row.has_kaspi_listing:
        badges.append(StatusBadge('Нет Kaspi', 'neutral'))
    elif row.listing_count > 1:
        badges.append(StatusBadge('Несколько listing', 'neutral'))
    if row.has_kaspi_listing and row.pp2 == 0:
        badges.append(StatusBadge('Нет PP2', 'warn'))
    if row.stock_delta not in (None, 0):
        badges.append(StatusBadge('Расхождение', 'off'))
    if row.has_kaspi_listing and row.our_price is None and row.listing_count == 1:
        badges.append(StatusBadge('Нет Kaspi цены', 'warn'))
    if row.returns_30 > 0:
        badges.append(StatusBadge('Возвраты', 'warn'))
    if row.has_kaspi_listing and row.sales_30 == 0:
        badges.append(StatusBadge('Нет продаж', 'neutral'))
    if not badges:
        badges.append(StatusBadge('OK', 'on'))
    rank = {text: index for index, text in enumerate(_BADGE_PRIORITY)}
    badges.sort(key=lambda item: rank.get(item.text, 99))
    return badges


def _safe_zpt_url(product: Product) -> str:
    try:
        url = product.get_absolute_url()
    except Exception:
        return ''
    return str(url or '').strip()


def _row_from_product(product: Product, sales: dict, economics) -> ProductWorkRow:
    listings = [
        ListingView(
            pk=item.pk,
            master_sku=item.master_sku,
            merchant_sku=item.merchant_sku,
            our_price=item.last_known_our_price,
            our_price_label=_format_kzt(item.last_known_our_price),
            kaspi_qty=item.last_known_kaspi_qty,
            kaspi_qty_label=(
                '—' if item.last_known_kaspi_qty is None else str(item.last_known_kaspi_qty)
            ),
            last_synced_at=item.last_synced_at,
            is_active=item.is_active,
            public_url=display_kaspi_public_url(item.public_url),
        )
        for item in product.kaspi_listings.all()
    ]
    listing_count = len(listings)
    stocks = _stock_map(product)
    pp1 = stocks[WAREHOUSE_CODE_PP1]
    pp2 = stocks[WAREHOUSE_CODE_PP2]
    our_price = listings[0].our_price if listing_count == 1 else None
    kaspi_qty = listings[0].kaspi_qty if listing_count == 1 else None
    our_price_label = _listing_price_label(listing_count, our_price)
    kaspi_qty_label = _listing_qty_label(listing_count, kaspi_qty)
    if listing_count == 1 and kaspi_qty is not None:
        stock_delta = kaspi_qty - pp2
    else:
        stock_delta = None
    zpt_url = _safe_zpt_url(product)
    kaspi_url = listings[0].public_url if listing_count == 1 else ''

    zpt_status = 'ZPT' if product.status == 'active' else 'скрыт'
    zpt_tone = 'on' if product.status == 'active' else 'neutral'
    econ_configured = False
    economics_note = 'Экономика не настроена'
    cost_price = product.cost_price
    commission_rate = None
    delivery_per_unit = None
    fulfillment_per_unit = None
    economic_floor = None
    contribution = None
    if economics is not None:
        missing = 'MISSING_ECONOMICS_CONFIG' in economics.reasons
        inactive = 'INACTIVE_CONFIG' in economics.reasons
        if missing or inactive or economics.fulfillment_per_unit is None:
            economics_note = 'Экономика не настроена'
        else:
            econ_configured = True
            economics_note = ''
            commission_rate = economics.expected_commission_rate
            delivery_per_unit = economics.expected_delivery_per_unit
            fulfillment_per_unit = economics.fulfillment_per_unit
            economic_floor = economics.calculated_min_price
            contribution = economics.contribution_profit

    row = ProductWorkRow(
        pk=product.pk,
        article=product.article or '—',
        title=product.title,
        brand=product.brand.name if product.brand_id else '—',
        category=product.category.name if product.category_id else '—',
        image_url=product.main_image.url if product.main_image else None,
        zpt_status=zpt_status,
        zpt_tone=zpt_tone,
        has_kaspi_listing=listing_count > 0,
        listing_count=listing_count,
        listings=listings,
        our_price=our_price,
        our_price_label=our_price_label,
        competitor_label='—',
        delta_label='—',
        rule_label='Не задано',
        recommended_label='—',
        pp1=pp1,
        pp2=pp2,
        kaspi_qty=kaspi_qty,
        kaspi_qty_label=kaspi_qty_label,
        stock_delta=stock_delta,
        sales_7=sales['sales_7'],
        sales_30=sales['sales_30'],
        returns_30=sales['returns_30'],
        sales_all=sales['sales_all'],
        purchases_all=sales['purchases_all'],
        returns_all=sales['returns_all'],
        net_gross_30=sales['net_gross_30'],
        net_gross_30_label=_format_kzt(sales['net_gross_30']),
        stock_delta_label=_format_delta(stock_delta),
        primary_badge=StatusBadge('OK', 'on'),
        extra_badge_count=0,
        status_title='OK',
        recommended_note='Правило снижения цены не задано',
        badges=[],
        zpt_url=zpt_url,
        kaspi_url=kaspi_url,
        public_url=zpt_url,
        economics_configured=econ_configured,
        cost_price=cost_price,
        commission_rate=commission_rate,
        delivery_per_unit=delivery_per_unit,
        fulfillment_per_unit=fulfillment_per_unit,
        economic_floor=economic_floor,
        contribution=contribution,
        economics_note=economics_note,
    )
    row.badges = _build_badges(row)
    row.primary_badge = row.badges[0]
    row.extra_badge_count = max(0, len(row.badges) - 1)
    row.status_title = ' · '.join(item.text for item in row.badges)
    return row


def _kpi_context(*, since_30):
    base = _annotate_workbench(Product.objects.all(), since_30=since_30)
    sales = _matched_ops(since=since_30).aggregate(
        purchases=Sum('quantity', filter=Q(operation_type=OP_PURCHASE)),
        returns=Sum('quantity', filter=Q(operation_type=OP_RETURN)),
    )
    purchases = int(sales['purchases'] or 0)
    returns = int(sales['returns'] or 0)
    return [
        {'label': 'Товаров', 'value': Product.objects.count()},
        {'label': 'На PP2', 'value': base.filter(pp2_qty__gt=0).count()},
        {'label': 'Нет на PP2', 'value': base.filter(pp2_qty=0).count()},
        {'label': 'Расхождение PP2 / Kaspi', 'value': base.filter(_mismatch_q()).count()},
        {'label': 'Продажи 30 дней', 'value': purchases - returns},
        {'label': 'Требуют внимания', 'value': base.filter(_attention_q()).count()},
    ]


def list_kaspi_products(params: QueryDict) -> dict:
    now = timezone.now()
    since_7 = now - timedelta(days=7)
    since_30 = now - timedelta(days=30)
    query = first_value(params, 'q')
    filter_key = first_value(params, 'filter')
    sort = first_value(params, 'sort') or SORT_DEFAULT
    allowed_sort = {key for key, _label in SORT_CHOICES}
    if sort not in allowed_sort:
        sort = SORT_DEFAULT
    category_chips = available_category_chips()
    allowed_filters = {key for key, _label in PAGE_FILTERS} | {
        key for key, _label in category_chips
    }
    if filter_key not in allowed_filters:
        filter_key = ''

    qs = Product.objects.select_related('brand', 'category', 'seller_profile')
    qs = _apply_search(qs, query)
    qs = _annotate_workbench(qs, since_30=since_30)
    qs = _apply_filter(qs, filter_key, category_chips)
    qs = _apply_sort(qs, sort, since_30=since_30)
    qs = qs.prefetch_related(
        Prefetch(
            'kaspi_listings',
            queryset=ProductKaspiListing.objects.order_by('id'),
        ),
        Prefetch(
            'warehouse_stocks',
            queryset=ProductWarehouseStock.objects.select_related('warehouse'),
        ),
    )
    page_result = paginate(qs, params)
    products = page_result.object_list
    sales_map = _sales_buckets(
        [item.pk for item in products],
        since_7=since_7,
        since_30=since_30,
    )
    economics_map = _economics_by_product(products)
    rows = [
        _row_from_product(
            product,
            sales_map[product.pk],
            economics_map.get(product.pk),
        )
        for product in products
    ]
    last_synced = ProductKaspiListing.objects.aggregate(ts=Max('last_synced_at'))['ts']
    chips = [PAGE_FILTERS[0]] + list(category_chips) + list(PAGE_FILTERS[1:])
    sort_links = [
        {
            'key': key,
            'label': label,
            'href': _querystring(params, sort=key),
        }
        for key, label in SORT_CHOICES
    ]
    return {
        'rows': rows,
        'page': page_result.page,
        'querystring': page_result.querystring,
        'total': page_result.total,
        'filters': {'q': query, 'filter': filter_key, 'sort': sort},
        'chips': [
            {
                'key': key,
                'label': label,
                'href': _querystring(params, filter=key),
            }
            for key, label in chips
        ],
        'sort_links': sort_links,
        'sort_hrefs': {item['key']: item['href'] for item in sort_links},
        'kpis': _kpi_context(since_30=since_30),
        'last_synced_at': last_synced,
        'economics_config_count': KaspiEconomicsConfig.objects.count(),
    }
