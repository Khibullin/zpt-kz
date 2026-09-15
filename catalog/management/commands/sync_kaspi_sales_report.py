from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.kaspi_sales_import import (
    STATUS_INVALID,
    sync_kaspi_sales_report,
)
from catalog.models import (
    CatalogImportBatch,
    KaspiOrder,
    KaspiSalesOperation,
    Product,
    ProductKaspiListing,
    ProductWarehouseStock,
    SellerProfile,
    StockMovement,
)


class Command(BaseCommand):
    help = (
        'Импорт исторического Kaspi Sales Report как read-only факты. '
        'По умолчанию dry-run. Не пишет склад, StockMovement, Product.price '
        'и last_known_* листинга. Не использует orders.Order.'
    )

    def add_arguments(self, parser):
        parser.add_argument('file', help='CSV или XLSX Sales Report Kaspi')
        parser.add_argument('--seller-profile-id', type=int, required=True)
        parser.add_argument('--sheet', default='', help='Имя листа XLSX')
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Записать KaspiOrder и KaspiSalesOperation. Без флага — отчёт.',
        )

    def handle(self, *args, **options):
        path = Path(options['file'])
        if not path.exists():
            raise CommandError(f'Не найден файл: {path}')
        try:
            seller = SellerProfile.objects.get(pk=options['seller_profile_id'])
        except SellerProfile.DoesNotExist as exc:
            raise CommandError(
                f'SellerProfile id={options["seller_profile_id"]} не найден'
            ) from exc

        apply = bool(options['apply'])
        before = _snapshot(seller)
        try:
            result = sync_kaspi_sales_report(
                path=path,
                seller=seller,
                apply=apply,
                sheet=(options.get('sheet') or '').strip(),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        after = _snapshot(seller)
        if after['warehouse'] != before['warehouse']:
            raise CommandError('importer изменил ProductWarehouseStock.')
        if after['movements'] != before['movements']:
            raise CommandError('importer создал или изменил StockMovement.')
        if after['products'] != before['products']:
            raise CommandError('importer изменил Product.price / cost_price / stock_qty.')
        if after['listings'] != before['listings']:
            raise CommandError(
                'importer изменил ProductKaspiListing.last_known_* / last_synced_at.'
            )
        if not apply and (
            after['orders'] != before['orders']
            or after['operations'] != before['operations']
            or after['batches'] != before['batches']
        ):
            raise CommandError('dry-run изменил Kaspi sales данные.')

        counts = result.summary
        mode = 'apply' if apply else 'dry-run'
        net_qty = counts['purchase_qty'] - counts['return_qty']
        net_gross = counts['purchase_gross'] + counts['return_gross']
        self.stdout.write(f'mode: {mode}')
        self.stdout.write(f'source: {result.source_path}')
        self.stdout.write(f'source_sha256: {result.source_sha256}')
        self.stdout.write(f'seller_profile: {seller.pk} {seller.name}')
        self.stdout.write(f'sheet: {result.sheet_name}')
        self.stdout.write(f'header_row: {result.header_row}')
        self.stdout.write(f'headers: {result.headers}')
        self.stdout.write(f'resolved_columns: {result.column_map}')
        self.stdout.write(f'Total rows: {counts["total_rows"]}')
        self.stdout.write(f'Purchases: {counts["purchases"]}')
        self.stdout.write(f'Returns: {counts["returns"]}')
        self.stdout.write(f'Valid rows: {counts["valid_rows"]}')
        self.stdout.write(f'Invalid rows: {counts["invalid_rows"]}')
        self.stdout.write(f'Matched listing: {counts["matched_listing"]}')
        self.stdout.write(f'Product only: {counts["product_only"]}')
        self.stdout.write(f'Unmatched product: {counts["unmatched_product"]}')
        self.stdout.write(f'Ambiguous product: {counts["ambiguous_product"]}')
        self.stdout.write(f'Would create orders: {counts["would_create_orders"]}')
        self.stdout.write(f'Existing orders: {counts["existing_orders"]}')
        self.stdout.write(
            f'Would create operations: {counts["would_create_operations"]}'
        )
        self.stdout.write(f'Already imported: {counts["already_imported"]}')
        self.stdout.write(f'Purchase qty: {counts["purchase_qty"]}')
        self.stdout.write(f'Return qty: {counts["return_qty"]}')
        self.stdout.write(f'Net sold qty: {net_qty}')
        self.stdout.write(f'Purchase gross amount: {counts["purchase_gross"]}')
        self.stdout.write(f'Return gross amount: {counts["return_gross"]}')
        self.stdout.write(f'Net gross amount: {net_gross}')
        self.stdout.write(f'Commission total: {counts["commission_total"]}')
        self.stdout.write(f'Delivery total: {counts["delivery_total"]}')

        problems = [row for row in result.rows if row.status == STATUS_INVALID]
        if problems:
            self.stdout.write('')
            self.stdout.write('Problem rows:')
            for row in problems:
                self.stdout.write(
                    f'row={row.row_number}\t'
                    f'order={row.external_order_id}\t'
                    f'type={row.operation_type}\t'
                    f'details={row.details}\t'
                    f'reason={row.reason}\t'
                    f'match_status={row.match_status}'
                )


def _snapshot(seller):
    return {
        'warehouse': list(
            ProductWarehouseStock.objects.order_by('pk').values_list(
                'pk', 'quantity'
            )
        ),
        'movements': list(
            StockMovement.objects.order_by('pk').values_list(
                'pk',
                'product_id',
                'warehouse_id',
                'movement_type',
                'quantity_delta',
                'source',
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
    }
