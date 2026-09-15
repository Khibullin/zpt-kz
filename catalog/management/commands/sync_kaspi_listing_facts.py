from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.kaspi_listing_facts import (
    PP2_COLUMN_WARNING,
    PROBLEM_STATUSES,
    summarize_kaspi_fact_rows,
    sync_kaspi_listing_facts,
)
from catalog.models import (
    KaspiListingFactSnapshot,
    Product,
    ProductKaspiListing,
    ProductWarehouseStock,
    SellerProfile,
    StockMovement,
)


class Command(BaseCommand):
    help = (
        'Read-only факты Kaspi listing: наблюдаемая цена и остаток площадки. '
        'Match только seller_profile + master_sku. '
        'По умолчанию dry-run. '
        'Не пишет Product, ProductWarehouseStock, StockMovement и Kaspi API. '
        'Колонка количества в выгрузке Kaspi (часто PP2) — observed qty площадки, '
        'не склад Rapido.'
    )

    def add_arguments(self, parser):
        parser.add_argument('file', help='XLSX или CSV с master SKU, ценой и остатком Kaspi')
        parser.add_argument('--seller-profile-id', type=int, required=True)
        parser.add_argument('--sheet', default='', help='Имя листа XLSX')
        parser.add_argument(
            '--sku-column',
            default='',
            help='Имя колонки Kaspi master SKU (например SKU)',
        )
        parser.add_argument(
            '--price-column',
            default='',
            help='Имя колонки наблюдаемой цены Kaspi',
        )
        parser.add_argument(
            '--quantity-column',
            default='',
            help='Имя колонки наблюдаемого остатка Kaspi (не Rapido PP2)',
        )
        parser.add_argument(
            '--source',
            default=KaspiListingFactSnapshot.SOURCE_ACTIVE_XLSX,
            choices=[
                KaspiListingFactSnapshot.SOURCE_ACTIVE_XLSX,
                KaspiListingFactSnapshot.SOURCE_MANUAL_IMPORT,
            ],
            help='Тип источника факта',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Записать cache listing + snapshot. Без флага — только отчёт.',
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
        listings_before = list(
            ProductKaspiListing.objects.filter(product__seller_profile=seller)
            .order_by('pk')
            .values_list(
                'pk',
                'last_known_our_price',
                'last_known_kaspi_qty',
                'last_synced_at',
            )
        )
        stocks_before = list(
            ProductWarehouseStock.objects.order_by('pk').values_list(
                'pk', 'quantity'
            )
        )
        moves_before = StockMovement.objects.count()
        products_before = list(
            Product.objects.filter(seller_profile=seller)
            .order_by('pk')
            .values_list('pk', 'price', 'cost_price', 'stock_qty')
        )
        snapshots_before = KaspiListingFactSnapshot.objects.count()

        try:
            result = sync_kaspi_listing_facts(
                path=path,
                seller=seller,
                apply=apply,
                sheet=(options.get('sheet') or '').strip(),
                sku_column=(options.get('sku_column') or '').strip(),
                price_column=(options.get('price_column') or '').strip(),
                quantity_column=(options.get('quantity_column') or '').strip(),
                source=options.get('source'),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        listings_after = list(
            ProductKaspiListing.objects.filter(product__seller_profile=seller)
            .order_by('pk')
            .values_list(
                'pk',
                'last_known_our_price',
                'last_known_kaspi_qty',
                'last_synced_at',
            )
        )
        stocks_after = list(
            ProductWarehouseStock.objects.order_by('pk').values_list(
                'pk', 'quantity'
            )
        )
        moves_after = StockMovement.objects.count()
        products_after = list(
            Product.objects.filter(seller_profile=seller)
            .order_by('pk')
            .values_list('pk', 'price', 'cost_price', 'stock_qty')
        )
        snapshots_after = KaspiListingFactSnapshot.objects.count()

        if stocks_after != stocks_before or moves_after != moves_before:
            raise CommandError('importer изменил ProductWarehouseStock или StockMovement.')
        if products_after != products_before:
            raise CommandError('importer изменил Product.price / cost_price / stock_qty.')
        if not apply and (
            listings_after != listings_before
            or snapshots_after != snapshots_before
        ):
            raise CommandError('dry-run изменил listing cache или snapshots.')

        counts = summarize_kaspi_fact_rows(result.rows)
        mode = 'apply' if apply else 'dry-run'
        self.stdout.write(f'mode: {mode}')
        self.stdout.write(f'source: {result.source_path}')
        self.stdout.write(f'source_sha256: {result.source_sha256}')
        self.stdout.write(f'seller_profile: {seller.pk} {seller.name}')
        self.stdout.write(f'sheet: {result.sheet_name}')
        self.stdout.write(f'headers: {result.headers}')
        self.stdout.write(f'resolved_columns: {result.column_map}')
        if result.quantity_header:
            self.stdout.write(f'quantity_column: {result.quantity_header}')
            if 'pp2' in result.quantity_header.lower().replace(' ', ''):
                self.stdout.write(PP2_COLUMN_WARNING)
        self.stdout.write(f'Total rows: {counts["total_rows"]}')
        self.stdout.write(f'Matched no change: {counts["matched_no_change"]}')
        self.stdout.write(f'Would update: {counts["would_update"]}')
        self.stdout.write(f'Updated: {counts["updated"]}')
        self.stdout.write(f'Missing listing: {counts["missing_listing"]}')
        self.stdout.write(f'Ambiguous: {counts["ambiguous_listing"]}')
        self.stdout.write(f'Invalid price: {counts["invalid_price"]}')
        self.stdout.write(f'Invalid quantity: {counts["invalid_quantity"]}')
        self.stdout.write(f'Duplicate master SKU: {counts["duplicate_master_sku"]}')
        self.stdout.write(f'In sync: {counts["in_sync"]}')
        self.stdout.write(f'Kaspi lower: {counts["kaspi_lower"]}')
        self.stdout.write(f'Kaspi higher: {counts["kaspi_higher"]}')
        self.stdout.write(f'No PP2 balance: {counts["no_pp2_balance"]}')
        self.stdout.write(
            f'Products with multiple listings: '
            f'{result.products_with_multiple_listings}'
        )

        self.stdout.write('')
        self.stdout.write(
            'row\tarticle\tproduct_id\tmaster_sku\tmerchant_sku\t'
            'kaspi_price\tkaspi_qty\tpp2_qty\tdelta\tstatus\trecon'
        )
        for row in result.rows:
            self.stdout.write(
                f'{row.row_number}\t'
                f'{row.product_article}\t'
                f'{row.product_id or ""}\t'
                f'{row.master_sku}\t'
                f'{row.merchant_sku}\t'
                f'{_qty(row.observed_price)}\t'
                f'{_qty(row.observed_qty)}\t'
                f'{_qty(row.pp2_qty)}\t'
                f'{_qty(row.qty_delta)}\t'
                f'{row.status}\t'
                f'{row.reconciliation}'
            )

        problem_rows = [
            row for row in result.rows if row.status in PROBLEM_STATUSES
        ]
        if problem_rows:
            self.stdout.write('')
            self.stdout.write('Problem rows:')
            for row in problem_rows:
                self.stdout.write(
                    f'row={row.row_number}\t'
                    f'master_sku={row.master_sku}\t'
                    f'status={row.status}\t'
                    f'reason={row.reason}'
                )


def _qty(value):
    if value is None:
        return ''
    return value
