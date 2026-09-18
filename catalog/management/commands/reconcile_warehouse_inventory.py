from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.data.rapido_pp2_2026_09_18 import NOTE, RAPIDO_PP2_QTY, REFERENCE
from catalog.models import Product, ProductKaspiListing, ProductWarehouseStock, StockMovement
from catalog.warehouse_reconciliation import (
    STATUS_ALREADY_APPLIED,
    STATUS_CHANGED,
    STATUS_UNCHANGED,
    STATUS_UNMATCHED,
    reconcile_warehouse_inventory,
)
from catalog.warehouses import WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2

SNAPSHOT_ALIASES = {
    'rapido-2026-09-18': RAPIDO_PP2_QTY,
    'PP2-RAPIDO-2026-09-18': RAPIDO_PP2_QTY,
}


class Command(BaseCommand):
    help = (
        'Сверка существующих остатков PP2 с полным снимком Rapido. '
        'Match только exact Product.article. Не создаёт Product. '
        'Строки склада, которых нет в снимке, не обнуляет. '
        'По умолчанию dry-run: запись только с явным --apply. '
        'Изменения = ADJUSTMENT, source=rapido_inventory. '
        'Не меняет PP1, Kaspi и Product.stock_qty.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--warehouse',
            default=WAREHOUSE_CODE_PP2,
            help='Код склада. Разрешён только PP2.',
        )
        parser.add_argument(
            '--file',
            default='',
            dest='file_path',
            help='XLSX со снимком (колонки артикула и количества).',
        )
        parser.add_argument(
            '--snapshot',
            default='',
            help='Встроенный снимок, например rapido-2026-09-18.',
        )
        parser.add_argument(
            '--reference',
            default=REFERENCE,
            help=f'Идемпотентный reference. По умолчанию {REFERENCE}.',
        )
        parser.add_argument(
            '--note',
            default=NOTE,
            help='Комментарий StockMovement.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Записать ADJUSTMENT. Без флага — только отчёт, без записи в БД.',
        )

    def handle(self, *args, **options):
        file_path = str(options.get('file_path') or '').strip()
        snapshot_name = str(options.get('snapshot') or '').strip()
        if bool(file_path) == bool(snapshot_name):
            raise CommandError('Укажите ровно один источник: --file или --snapshot.')
        mapping = None
        path = None
        if snapshot_name:
            mapping = SNAPSHOT_ALIASES.get(snapshot_name)
            if mapping is None:
                raise CommandError(f'unknown_snapshot:{snapshot_name}')
        else:
            path = Path(file_path)
            if not path.exists():
                raise CommandError(f'Не найден файл: {path}')

        apply = bool(options['apply'])
        before = _safety_snapshot()
        try:
            result = reconcile_warehouse_inventory(
                path=path,
                mapping=mapping,
                warehouse_code=str(options['warehouse'] or '').strip(),
                reference=str(options['reference'] or '').strip(),
                note=str(options['note'] or '').strip(),
                apply=apply,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        after = _safety_snapshot()
        _assert_safety(before, after, apply=apply)

        summary = result.summary
        mode = 'apply' if apply else 'dry-run'
        self.stdout.write(f'mode: {mode}')
        self.stdout.write(f'source: {result.source_path}')
        self.stdout.write(f'sheet: {result.sheet_name}')
        self.stdout.write(f'headers: {result.headers}')
        self.stdout.write(
            f'warehouse: {result.warehouse_code} {result.warehouse_name}'
        )
        self.stdout.write(f'reference: {result.reference}')
        self.stdout.write(f'note: {result.note}')
        self.stdout.write(f'source rows = {summary["source_rows"]}')
        self.stdout.write(f'matched = {summary["matched"]}')
        self.stdout.write(f'unmatched = {summary["unmatched"]}')
        self.stdout.write(f'source total = {summary["source_total"]}')
        self.stdout.write(f'existing PP2 total = {summary["existing_pp2_total"]}')
        self.stdout.write(f'changed = {summary["changed"]}')
        self.stdout.write(f'unchanged = {summary["unchanged"]}')
        self.stdout.write(f'already applied = {summary["already_applied"]}')
        self.stdout.write(f'net delta = {summary["net_delta"]}')
        self.stdout.write(f'result total = {summary["result_total"]}')
        if apply and summary.get('persisted_pp2_total') is not None:
            self.stdout.write(
                f'persisted PP2 total = {summary["persisted_pp2_total"]}'
            )

        unmatched = [row for row in result.rows if row.status == STATUS_UNMATCHED]
        self.stdout.write('')
        self.stdout.write('UNMATCHED:')
        if unmatched:
            for row in unmatched:
                self.stdout.write(f'{row.article} | {row.qty}')
        else:
            self.stdout.write('(none)')

        changed = [row for row in result.rows if row.status == STATUS_CHANGED]
        self.stdout.write('')
        self.stdout.write('CHANGED:')
        if changed:
            for row in changed:
                self.stdout.write(
                    f'{row.article}\t{row.quantity_before} -> {row.quantity_after} '
                    f'delta={row.quantity_delta}'
                )
        else:
            self.stdout.write('(none)')

        already = [
            row for row in result.rows if row.status == STATUS_ALREADY_APPLIED
        ]
        unchanged = [row for row in result.rows if row.status == STATUS_UNCHANGED]
        self.stdout.write('')
        self.stdout.write(
            f'{mode} CHANGED rows: {len(changed)} '
            f'UNCHANGED={len(unchanged)} ALREADY_APPLIED={len(already)} '
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
    movements = list(
        StockMovement.objects.order_by('pk').values_list(
            'pk', 'warehouse_id', 'product_id', 'movement_type', 'quantity_delta'
        )
    )
    return {
        'products': products,
        'listings': listings,
        'pp1': pp1,
        'movements': movements,
    }


def _assert_safety(before, after, *, apply):
    if after['products'] != before['products']:
        raise CommandError(
            'reconciler изменил Product.stock_qty / price / cost_price / status.'
        )
    if after['listings'] != before['listings']:
        raise CommandError('reconciler изменил ProductKaspiListing.')
    if after['pp1'] != before['pp1']:
        raise CommandError('reconciler изменил PP1.')
    if not apply and after['movements'] != before['movements']:
        raise CommandError('dry-run изменил StockMovement.')
