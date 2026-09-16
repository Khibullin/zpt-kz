from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.models import Product, ProductKaspiListing, ProductWarehouseStock, StockMovement
from catalog.warehouse_inventory_import import (
    STATUS_ALREADY_IMPORTED,
    STATUS_BLANK_QTY_MATCHED,
    STATUS_CONFLICT_EXISTING_BALANCE,
    STATUS_CREATE_OPENING,
    STATUS_DUPLICATE_ARTICLE,
    STATUS_ERROR,
    STATUS_UNMATCHED,
    import_warehouse_inventory,
)
from catalog.warehouses import WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2


class Command(BaseCommand):
    help = (
        'Импорт физической инвентаризации склада (PP1/PP2) из Excel. '
        'Match только exact Product.article. Не создаёт Product. '
        'По умолчанию dry-run: запись только с явным --apply. '
        'Первый остаток = OPENING. Существующий баланс = CONFLICT, без overwrite. '
        'Blank qty не считается 0. Не меняет PP другого склада, Kaspi и Product.stock_qty.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--warehouse',
            required=True,
            help='Код склада, например PP1 или PP2.',
        )
        parser.add_argument(
            '--file',
            required=True,
            dest='file_path',
            help='XLSX с колонками артикула и количества.',
        )
        parser.add_argument(
            '--inventory-date',
            required=True,
            help='Дата инвентаризации YYYY-MM-DD. Пишется в reference/note, не в created_at.',
        )
        parser.add_argument(
            '--article-column',
            default='Артикул',
            help='Имя колонки артикула. По умолчанию: Артикул.',
        )
        parser.add_argument(
            '--quantity-column',
            default='Наличие на складе АГ',
            help='Имя колонки количества. По умолчанию: Наличие на складе АГ.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Записать OPENING остатки. Без флага — только отчёт, без записи в БД.',
        )

    def handle(self, *args, **options):
        path = Path(options['file_path'])
        if not path.exists():
            raise CommandError(f'Не найден файл: {path}')

        warehouse_code = str(options['warehouse'] or '').strip()
        apply = bool(options['apply'])
        before = _safety_snapshot()
        try:
            result = import_warehouse_inventory(
                path=path,
                warehouse_code=warehouse_code,
                inventory_date=options['inventory_date'],
                apply=apply,
                article_column=options['article_column'],
                quantity_column=options['quantity_column'],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        after = _safety_snapshot()
        _assert_safety(before, after, apply=apply, warehouse_code=result.warehouse_code)

        summary = result.summary
        mode = 'apply' if apply else 'dry-run'
        self.stdout.write(f'mode: {mode}')
        self.stdout.write(f'source: {result.source_path}')
        self.stdout.write(f'sheet: {result.sheet_name}')
        self.stdout.write(f'headers: {result.headers}')
        self.stdout.write(
            f'warehouse: {result.warehouse_code} {result.warehouse_name}'
        )
        self.stdout.write(f'inventory_date: {result.inventory_date.isoformat()}')
        self.stdout.write(f'reference: {result.reference}')
        self.stdout.write(f'source rows = {summary["source_rows"]}')
        self.stdout.write(f'known qty = {summary["known_qty"]}')
        self.stdout.write(f'blank qty = {summary["blank_qty"]}')
        self.stdout.write(f'matched products = {summary["matched_products"]}')
        self.stdout.write(f'unmatched products = {summary["unmatched_products"]}')
        self.stdout.write(f'safe write candidates = {summary["safe_write_candidates"]}')
        self.stdout.write(f'candidate qty = {summary["candidate_qty"]}')

        self.stdout.write('')
        self.stdout.write('UNMATCHED:')
        unmatched = [row for row in result.rows if row.status == STATUS_UNMATCHED]
        if unmatched:
            for row in unmatched:
                qty = 'blank' if row.qty is None else row.qty
                self.stdout.write(f'{row.article} | {qty}')
        else:
            self.stdout.write('(none)')

        self.stdout.write('')
        self.stdout.write('BLANK_QTY_MATCHED:')
        blanks = [row for row in result.rows if row.status == STATUS_BLANK_QTY_MATCHED]
        if blanks:
            for row in blanks:
                self.stdout.write(row.article)
        else:
            self.stdout.write('(none)')

        conflicts = [
            row for row in result.rows
            if row.status == STATUS_CONFLICT_EXISTING_BALANCE
        ]
        already = [
            row for row in result.rows if row.status == STATUS_ALREADY_IMPORTED
        ]
        errors = [row for row in result.rows if row.status == STATUS_ERROR]
        duplicates = [
            row for row in result.rows if row.status == STATUS_DUPLICATE_ARTICLE
        ]
        if conflicts or already or errors or duplicates:
            self.stdout.write('')
            self.stdout.write('Other skipped/error rows:')
            for row in (*duplicates, *errors, *conflicts, *already):
                self.stdout.write(
                    f'row={row.row_number}\tarticle={row.article}\t'
                    f'status={row.status}\treason={row.reason}'
                )

        created = [row for row in result.rows if row.status == STATUS_CREATE_OPENING]
        self.stdout.write('')
        self.stdout.write(
            f'{mode} CREATE_OPENING rows: {len(created)} '
            f'(writes={"yes" if apply else "no"})'
        )


def _safety_snapshot():
    products = list(
        Product.objects.order_by('pk').values_list(
            'pk', 'stock_qty', 'price', 'cost_price', 'status'
        )
    )
    listings = list(
        ProductKaspiListing.objects.order_by('pk').values_list(
            'pk',
            'last_known_our_price',
            'last_known_kaspi_qty',
            'public_url',
            'last_synced_at',
        )
    )
    pp1 = list(
        ProductWarehouseStock.objects.filter(warehouse__code=WAREHOUSE_CODE_PP1)
        .order_by('pk')
        .values_list('pk', 'product_id', 'quantity')
    )
    pp2 = list(
        ProductWarehouseStock.objects.filter(warehouse__code=WAREHOUSE_CODE_PP2)
        .order_by('pk')
        .values_list('pk', 'product_id', 'quantity')
    )
    movements = list(
        StockMovement.objects.order_by('pk').values_list(
            'pk', 'warehouse_id', 'product_id', 'movement_type', 'quantity_delta'
        )
    )
    return {
        'products': products,
        'listings': listings,
        'pp1': pp1,
        'pp2': pp2,
        'movements': movements,
    }


def _assert_safety(before, after, *, apply, warehouse_code):
    if after['products'] != before['products']:
        raise CommandError(
            'importer изменил Product.stock_qty / price / cost_price / status.'
        )
    if after['listings'] != before['listings']:
        raise CommandError('importer изменил ProductKaspiListing.')
    if after['pp2'] != before['pp2'] and warehouse_code != WAREHOUSE_CODE_PP2:
        raise CommandError('importer изменил PP2.')
    if after['pp1'] != before['pp1'] and warehouse_code != WAREHOUSE_CODE_PP1:
        raise CommandError('importer изменил PP1.')
    if not apply and after != before:
        raise CommandError('dry-run изменил складские или товарные данные.')
