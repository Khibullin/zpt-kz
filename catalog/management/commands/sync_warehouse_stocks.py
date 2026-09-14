from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.models import Product, ProductWarehouseStock, SellerProfile, StockMovement
from catalog.warehouse_stock_sync import (
    KASPI_PP_WARNING,
    PROBLEM_STATUSES,
    STATUS_MATCHED_NO_CHANGE,
    summarize_warehouse_stock_rows,
    sync_warehouse_stocks,
    write_warehouse_stock_report,
)
from catalog.warehouses import (
    DEFAULT_WAREHOUSES,
    WAREHOUSE_CODE_PP1,
    WAREHOUSE_CODE_PP2,
)


class Command(BaseCommand):
    help = (
        'Импорт остатков ProductWarehouseStock из XLSX/CSV. '
        'Match только seller_profile + article. '
        'По умолчанию dry-run: ничего не пишет в БД. '
        'Запись только с явным --apply: первый остаток = OPENING, '
        'дальнейшие сверки = ADJUSTMENT. '
        'Не создаёт Product, не меняет Product.stock_qty, цены и Kaspi.'
    )

    def add_arguments(self, parser):
        parser.add_argument('file', help='XLSX или CSV с артикулом и остатками')
        parser.add_argument('--seller-profile-id', type=int, required=True)
        parser.add_argument(
            '--warehouse-code',
            default='',
            help='Один склад (PP1 или PP2), если в файле одна колонка количества',
        )
        parser.add_argument(
            '--quantity-column',
            default='',
            help='Имя колонки количества для режима --warehouse-code',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help=(
                'Записать остатки. Первый баланс = OPENING, '
                'последующие изменения = ADJUSTMENT. Без флага — только отчёт.'
            ),
        )
        parser.add_argument(
            '--confirm-pp-source',
            action='store_true',
            help=(
                'Подтвердить, что запись в PP1/PP2 — склады ZPT, '
                'а не пункты выдачи Kaspi. Обязателен для любого '
                '--apply, который пишет PP1 или PP2, включая '
                '--warehouse-code PP2.'
            ),
        )
        parser.add_argument(
            '--report',
            default='',
            help='Путь к CSV или XLSX отчёту',
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
        stocks_before = ProductWarehouseStock.objects.count()
        movements_before = StockMovement.objects.count()
        stock_qty_before = list(
            Product.objects.filter(seller_profile=seller)
            .order_by('pk')
            .values_list('pk', 'stock_qty')
        )
        try:
            result = sync_warehouse_stocks(
                path=path,
                seller=seller,
                apply=apply,
                warehouse_code=(options.get('warehouse_code') or '').strip(),
                quantity_column=(options.get('quantity_column') or '').strip(),
                confirm_pp_source=bool(options.get('confirm_pp_source')),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        stocks_after = ProductWarehouseStock.objects.count()
        movements_after = StockMovement.objects.count()
        stock_qty_after = list(
            Product.objects.filter(seller_profile=seller)
            .order_by('pk')
            .values_list('pk', 'stock_qty')
        )
        if not apply and (
            stocks_after != stocks_before
            or movements_after != movements_before
            or stock_qty_after != stock_qty_before
        ):
            raise CommandError('dry-run изменил складские данные или Product.stock_qty.')
        if stock_qty_after != stock_qty_before:
            raise CommandError('importer изменил Product.stock_qty.')

        counts = summarize_warehouse_stock_rows(result.rows)
        mode = 'apply' if apply else 'dry-run'
        names = dict(DEFAULT_WAREHOUSES)
        self.stdout.write(f'mode: {mode}')
        self.stdout.write(f'source: {result.source_path}')
        self.stdout.write(f'seller_profile: {seller.pk} {seller.name}')
        self.stdout.write(f'sheet: {result.sheet_name}')
        self.stdout.write(f'headers: {result.headers}')
        self.stdout.write(f'warehouses: {result.warehouse_codes}')
        for code in result.warehouse_codes:
            self.stdout.write(f'  {code} = {names.get(code, code)}')
        if result.dual_pp_columns or any(
            code in {WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2}
            for code in result.warehouse_codes
        ):
            self.stdout.write(KASPI_PP_WARNING)
        for label, values in result.samples.items():
            self.stdout.write(f'sample {label}: {values}')
        self.stdout.write(f'Total rows: {counts["total_rows"]}')
        self.stdout.write(f'Matched no change: {counts["matched_no_change"]}')
        self.stdout.write(f'Would create balance: {counts["would_create_balance"]}')
        self.stdout.write(f'Would adjust: {counts["would_adjust"]}')
        self.stdout.write(f'Missing products: {counts["missing_products"]}')
        self.stdout.write(f'Invalid quantity: {counts["invalid_quantity"]}')
        self.stdout.write(f'Duplicate article: {counts["duplicate_article"]}')
        self.stdout.write(f'Invalid rows: {counts["invalid_rows"]}')

        self.stdout.write('')
        self.stdout.write(
            'article\tproduct_id\told_pp1\tnew_pp1\tdelta_pp1\t'
            'old_pp2\tnew_pp2\tdelta_pp2\tstatus'
        )
        for row in result.rows:
            self.stdout.write(
                f'{row.article}\t'
                f'{row.product_id or ""}\t'
                f'{_qty(row.pp1.old_qty)}\t{_qty(row.pp1.new_qty)}\t{_qty(row.pp1.delta)}\t'
                f'{_qty(row.pp2.old_qty)}\t{_qty(row.pp2.new_qty)}\t{_qty(row.pp2.delta)}\t'
                f'{row.status}'
            )

        problem_rows = [
            row for row in result.rows
            if row.status in PROBLEM_STATUSES or row.status != STATUS_MATCHED_NO_CHANGE
        ]
        if problem_rows:
            self.stdout.write('')
            self.stdout.write('Non-matched rows:')
            for row in problem_rows:
                self.stdout.write(
                    f'row={row.row_number}\t'
                    f'article={row.article}\t'
                    f'status={row.status}\t'
                    f'reason={row.reason}'
                )

        report = (options['report'] or '').strip()
        if report:
            saved = write_warehouse_stock_report(result.rows, Path(report))
            self.stdout.write(f'report: {saved}')


def _qty(value):
    if value is None:
        return ''
    return value
