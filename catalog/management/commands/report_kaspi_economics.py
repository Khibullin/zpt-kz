from datetime import datetime, time as dt_time
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalog.kaspi_economics import (
    product_is_problem,
    report_listing_economics,
    report_product_economics,
)
from catalog.models import (
    CatalogImportBatch,
    KaspiEconomicsConfig,
    KaspiOrder,
    KaspiSalesOperation,
    Product,
    ProductKaspiEconomicsPolicy,
    ProductKaspiListing,
    ProductWarehouseStock,
    SellerProfile,
    StockMovement,
)


class Command(BaseCommand):
    help = (
        'Read-only отчёт товарной экономики Kaspi. '
        'Не пишет БД, не меняет цены, склад и listing facts.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--seller-profile-id', type=int, required=True)
        parser.add_argument('--product-id', type=int, default=None)
        parser.add_argument('--article', default='')
        parser.add_argument('--listing-id', type=int, default=None)
        parser.add_argument('--only-problems', action='store_true')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--date-from', default='')
        parser.add_argument('--date-to', default='')

    def handle(self, *args, **options):
        try:
            seller = SellerProfile.objects.get(pk=options['seller_profile_id'])
        except SellerProfile.DoesNotExist as exc:
            raise CommandError(
                f'SellerProfile id={options["seller_profile_id"]} не найден'
            ) from exc

        date_from = _parse_dt(options.get('date_from'))
        date_to = _parse_dt(options.get('date_to'), end_of_day=True)
        if (
            date_from is not None
            and date_to is not None
            and date_from > date_to
        ):
            raise CommandError('Некорректный период: date-from больше date-to')
        before = _snapshot(seller)
        listing_id = options.get('listing_id')
        if listing_id:
            seller_result, listing_result = report_listing_economics(
                seller,
                listing_id,
                date_from=date_from,
                date_to=date_to,
            )
            product_results = []
        else:
            seller_result, product_results = report_product_economics(
                seller,
                date_from=date_from,
                date_to=date_to,
                product_id=options.get('product_id'),
                article=(options.get('article') or '').strip() or None,
            )
            listing_result = None
        after = _snapshot(seller)
        if after != before:
            raise CommandError('report_kaspi_economics изменил БД.')

        self.stdout.write('MODE: READ ONLY')
        self.stdout.write(f'Seller: {seller.pk} {seller.name}')
        config = seller_result.config
        if config is None:
            self.stdout.write('Config: MISSING')
        else:
            self.stdout.write(
                f'Config: packaging={config.fulfillment_packaging_per_unit} '
                f'handling={config.fulfillment_handling_per_unit} '
                f'fulfillment_total={config.fulfillment_total_per_unit} '
                f'default_min_margin={config.default_min_margin_percent}% '
                f'active={config.is_active}'
            )
        self.stdout.write('')
        self.stdout.write('SELLER HISTORY')
        self.stdout.write(f'Orders: {seller_result.orders}')
        self.stdout.write(f'Operations: {seller_result.operations}')
        self.stdout.write(f'Purchases: {seller_result.purchase_ops}')
        self.stdout.write(f'Returns: {seller_result.return_ops}')
        self.stdout.write(f'Purchase qty: {seller_result.purchase_qty}')
        self.stdout.write(f'Return qty: {seller_result.return_qty}')
        self.stdout.write(f'Net qty: {seller_result.net_qty}')
        self.stdout.write(f'Purchase gross: {seller_result.purchase_gross}')
        self.stdout.write(f'Return gross: {seller_result.return_gross}')
        self.stdout.write(f'Net gross: {seller_result.net_gross}')
        self.stdout.write(f'Commission cost: {seller_result.commission_cost_total}')
        self.stdout.write(
            f'Effective commission rate: '
            f'{_pct(seller_result.effective_purchase_commission_rate)}'
        )
        self.stdout.write(f'Kaspi delivery cost: {seller_result.delivery_cost_total}')
        self.stdout.write(
            f'Avg delivery per purchase unit: '
            f'{seller_result.avg_purchase_delivery_per_unit}'
        )
        self.stdout.write('Match quality:')
        self.stdout.write(f'Listing matched: {seller_result.listing_matched}')
        self.stdout.write(f'Product only: {seller_result.product_only}')
        self.stdout.write(f'Unmatched: {seller_result.unmatched}')
        self.stdout.write(f'Ambiguous: {seller_result.ambiguous}')
        self.stdout.write(f'Unmatched gross: {seller_result.unmatched_gross}')

        if listing_result is not None:
            self._print_listing(listing_result)
            return

        if options.get('only_problems'):
            product_results = [
                row for row in product_results if product_is_problem(row)
            ]
        limit = int(options.get('limit') or 0)
        if limit > 0:
            product_results = product_results[:limit]

        history_count = sum(
            1 for row in product_results if row.purchase_qty or row.return_qty
        )
        with_cost = sum(1 for row in product_results if row.cost_price is not None)
        with_price = sum(1 for row in product_results if row.current_price is not None)
        floor_ready = sum(1 for row in product_results if row.is_calculated_floor_ready)
        below = sum(
            1 for row in product_results
            if 'BELOW_MIN_PRICE' in row.reasons
        )
        missing_cost = sum(
            1 for row in product_results if 'MISSING_COST_PRICE' in row.reasons
        )
        no_commission = sum(
            1 for row in product_results if 'NO_COMMISSION_HISTORY' in row.reasons
        )
        no_delivery = sum(
            1 for row in product_results if 'NO_DELIVERY_HISTORY' in row.reasons
        )
        self.stdout.write('')
        self.stdout.write('PRODUCT SUMMARY')
        self.stdout.write(f'Total products: {len(product_results)}')
        self.stdout.write(f'Products with Kaspi history: {history_count}')
        self.stdout.write(f'Products with cost_price: {with_cost}')
        self.stdout.write(f'Products with current price: {with_price}')
        self.stdout.write(f'Products floor-ready: {floor_ready}')
        self.stdout.write(f'Products below floor: {below}')
        self.stdout.write(f'Missing cost: {missing_cost}')
        self.stdout.write(f'No commission history: {no_commission}')
        self.stdout.write(f'No delivery history: {no_delivery}')
        self.stdout.write('')
        self.stdout.write(
            'product_id\tarticle\tname_short\tlisting_count\t'
            'purchase_qty\treturn_qty\tnet_qty\tnet_gross\tcost_price\t'
            'commission_rate\tcommission_source\tdelivery_per_unit\t'
            'delivery_source\tfulfillment_per_unit\testimated_fulfillment_total\t'
            'historical_contribution\t'
            'contribution_margin\tmin_margin\tcalculated_min_price\t'
            'manual_min_price\teffective_min_price\tcurrent_price\t'
            'headroom\tstatus'
        )
        for row in product_results:
            self.stdout.write(
                '\t'.join([
                    str(row.product_id or ''),
                    row.article,
                    (row.title or '')[:40],
                    str(row.listing_count),
                    str(row.purchase_qty),
                    str(row.return_qty),
                    str(row.net_qty),
                    _fmt(row.net_gross),
                    _fmt(row.cost_price),
                    _fmt(row.expected_commission_rate),
                    row.expected_commission_source,
                    _fmt(row.expected_delivery_per_unit),
                    row.expected_delivery_source,
                    _fmt(row.fulfillment_per_unit),
                    _fmt(row.estimated_fulfillment_total),
                    _fmt(row.contribution_profit),
                    _fmt(row.contribution_margin),
                    _fmt(row.min_margin_percent),
                    _fmt(row.calculated_min_price),
                    _fmt(row.manual_min_price),
                    _fmt(row.effective_min_price),
                    _fmt(row.current_price),
                    _fmt(row.price_headroom),
                    row.primary_status,
                ])
            )

    def _print_listing(self, row):
        self.stdout.write('')
        self.stdout.write('LISTING REPORT')
        self.stdout.write(f'listing_id: {row.listing_id}')
        self.stdout.write(f'product_id: {row.product_id}')
        self.stdout.write(f'article: {row.article}')
        self.stdout.write(f'master_sku: {row.master_sku}')
        self.stdout.write(f'merchant_sku: {row.merchant_sku}')
        self.stdout.write(f'current_price: {row.current_price}')
        self.stdout.write(f'kaspi_observed_qty: {row.last_known_kaspi_qty}')
        self.stdout.write(
            f'history purchase_qty={row.purchase_qty} return_qty={row.return_qty} '
            f'net_qty={row.net_qty} net_gross={row.net_gross} '
            f'commission_cost={row.commission_cost_total} '
            f'delivery_cost={row.delivery_cost_total} '
            f'estimated_fulfillment_total={row.estimated_fulfillment_total}'
        )
        self.stdout.write(
            f'expected commission={row.expected_commission_rate} '
            f'source={row.expected_commission_source}'
        )
        self.stdout.write(
            f'expected delivery={row.expected_delivery_per_unit} '
            f'source={row.expected_delivery_source}'
        )
        self.stdout.write(
            f'cost={row.cost_price} fulfillment={row.fulfillment_per_unit} '
            f'min_margin={row.min_margin_percent}%'
        )
        self.stdout.write(
            f'calculated_min_price={row.calculated_min_price} '
            f'manual_min_price={row.manual_min_price} '
            f'effective_min_price={row.effective_min_price} '
            f'headroom={row.price_headroom}'
        )
        self.stdout.write(
            f'Product-only operations not allocated to listing: '
            f'{row.product_only_ops}'
        )
        self.stdout.write(f'status={row.primary_status}')
        self.stdout.write(f'reasons={",".join(row.reasons)}')


def _parse_dt(raw, *, end_of_day=False):
    text = (raw or '').strip()
    if not text:
        return None
    parsed = None
    date_only = False
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
        try:
            parsed = datetime.strptime(text, fmt)
            date_only = fmt == '%Y-%m-%d'
            break
        except ValueError:
            continue
    if parsed is None:
        raise CommandError(f'Некорректная дата: {text}')
    if date_only and end_of_day:
        parsed = datetime.combine(parsed.date(), dt_time.max)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _fmt(value):
    if value is None:
        return ''
    if isinstance(value, Decimal):
        return format(value, 'f')
    return str(value)


def _pct(value):
    if value is None:
        return ''
    return f'{format(value * Decimal("100"), "f")}%'


def _snapshot(seller):
    return {
        'warehouse': list(
            ProductWarehouseStock.objects.order_by('pk').values_list('pk', 'quantity')
        ),
        'movements': list(
            StockMovement.objects.order_by('pk').values_list(
                'pk', 'product_id', 'warehouse_id', 'movement_type', 'quantity_delta'
            )
        ),
        'products': list(
            Product.objects.filter(seller_profile=seller)
            .order_by('pk')
            .values_list('pk', 'price', 'cost_price', 'stock_qty')
        ),
        'listings': list(
            ProductKaspiListing.objects.filter(product__seller_profile=seller)
            .order_by('pk')
            .values_list(
                'pk',
                'last_known_our_price',
                'last_known_kaspi_qty',
                'last_synced_at',
            )
        ),
        'orders': KaspiOrder.objects.filter(seller_profile=seller).count(),
        'operations': KaspiSalesOperation.objects.filter(
            seller_profile=seller
        ).count(),
        'batches': CatalogImportBatch.objects.filter(seller_profile=seller).count(),
        'configs': KaspiEconomicsConfig.objects.filter(seller_profile=seller).count(),
        'policies': ProductKaspiEconomicsPolicy.objects.filter(
            product__seller_profile=seller
        ).count(),
    }
