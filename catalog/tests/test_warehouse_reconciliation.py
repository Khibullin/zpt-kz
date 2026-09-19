from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from catalog.ag_parts_import import extract_article
from catalog.data.rapido_pp2_2026_09_18 import (
    HOLD_DUPLICATE_ARTICLES,
    KNOWN_CHANGES,
    NO_LISTING_ARTICLES,
    NOTE,
    RAPIDO_PP2_QTY,
    REFERENCE,
    rapido_pp2_before_qty,
)
from catalog.data.rapido_pp2_2026_09_18_1817 import (
    KNOWN_CHANGES as KNOWN_CHANGES_1817,
    NOTE as NOTE_1817,
    RAPIDO_PP2_QTY as RAPIDO_PP2_1817_QTY,
    REFERENCE as REFERENCE_1817,
    rapido_pp2_before_qty as rapido_pp2_1817_before_qty,
)
from catalog.kaspi_stock_update_preview import (
    STATUS_HOLD_DUPLICATE,
    STATUS_NO_LISTING,
    STATUS_READY,
    build_preview_from_listings,
    write_kaspi_stock_preview_xlsx,
)
from catalog.models import (
    Product,
    ProductKaspiListing,
    ProductWarehouseStock,
    SellerProfile,
    StockMovement,
    Warehouse,
)
from catalog.stock_service import apply_stock_movement, get_stock_quantity
from catalog.warehouse_reconciliation import (
    SOURCE_RAPIDO,
    STATUS_ALREADY_APPLIED,
    STATUS_CHANGED,
    STATUS_UNCHANGED,
    STATUS_UNMATCHED,
    reconcile_warehouse_inventory,
)
from catalog.warehouses import WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2
from repricer.models import (
    KaspiCompetitorOfferSnapshot,
    KaspiOwnPriceSnapshot,
    KaspiRepricerRecommendation,
    KaspiRepricerRule,
)


def _xlsx(path, rows, headers=('article', 'qty')):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'PP2'
    sheet.append(list(headers))
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _make_seller(username='rapido-seller'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=username,
        phone='77011112241',
        city='Алматы',
    )


def _make_product(seller, article, **kwargs):
    defaults = {
        'title': f'Title {article}',
        'article': article,
        'seller_name': seller.name,
        'whatsapp_number': seller.phone,
        'seller_profile': seller,
        'city': seller.city,
        'status': 'active',
        'price': 1000,
        'cost_price': 400,
        'stock_qty': 7,
        'publish_to_kaspi': True,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _open_stock(product, warehouse, qty, source='seed'):
    return apply_stock_movement(
        product=product,
        warehouse=warehouse,
        movement_type=StockMovement.MovementType.OPENING,
        quantity_delta=qty,
        source=source,
    )


class RapidoSnapshotContractTests(TestCase):
    def test_snapshot_counts(self):
        self.assertEqual(len(RAPIDO_PP2_QTY), 62)
        self.assertEqual(sum(RAPIDO_PP2_QTY.values()), 1898)
        self.assertEqual(len(KNOWN_CHANGES), 13)
        self.assertEqual(len(HOLD_DUPLICATE_ARTICLES), 10)
        self.assertEqual(NO_LISTING_ARTICLES, {'F081109111HD'})
        before = rapido_pp2_before_qty()
        self.assertEqual(len(before), 62)
        self.assertEqual(sum(before.values()), 1996)
        self.assertEqual(sum(before.values()) - sum(RAPIDO_PP2_QTY.values()), 98)
        self.assertEqual(RAPIDO_PP2_QTY['151000187AA'], 37)
        self.assertEqual(RAPIDO_PP2_QTY['1109140W5000'], 2)


class RapidoSnapshot1817ContractTests(TestCase):
    def test_snapshot_counts_and_deltas(self):
        self.assertEqual(len(RAPIDO_PP2_1817_QTY), 62)
        self.assertEqual(sum(RAPIDO_PP2_1817_QTY.values()), 1896)
        self.assertEqual(set(RAPIDO_PP2_1817_QTY), set(RAPIDO_PP2_QTY))
        self.assertEqual(len(KNOWN_CHANGES_1817), 2)
        self.assertEqual(REFERENCE_1817, 'PP2-RAPIDO-2026-09-18-1817')
        self.assertEqual(NOTE_1817, 'Rapido physical inventory snapshot 2026-09-18 18:17')
        self.assertEqual(RAPIDO_PP2_QTY['151000187AA'], 37)
        self.assertEqual(RAPIDO_PP2_QTY['1109140W5000'], 2)
        self.assertEqual(RAPIDO_PP2_1817_QTY['151000187AA'], 36)
        self.assertEqual(RAPIDO_PP2_1817_QTY['1109140W5000'], 1)
        self.assertEqual(KNOWN_CHANGES_1817['151000187AA'], (37, 36))
        self.assertEqual(KNOWN_CHANGES_1817['1109140W5000'], (2, 1))
        before = rapido_pp2_1817_before_qty()
        self.assertEqual(len(before), 62)
        self.assertEqual(sum(before.values()), 1898)
        self.assertEqual(sum(before.values()) - sum(RAPIDO_PP2_1817_QTY.values()), 2)
        unchanged = [
            article
            for article, qty in RAPIDO_PP2_1817_QTY.items()
            if article not in KNOWN_CHANGES_1817
        ]
        self.assertEqual(len(unchanged), 60)
        for article in unchanged:
            self.assertEqual(RAPIDO_PP2_1817_QTY[article], RAPIDO_PP2_QTY[article])

    def test_cabin_filter_row_extracts_exact_article(self):
        article, article_key = extract_article('Салонный фильтр 8104400XP24BA')
        self.assertEqual(article, '8104400XP24BA')
        self.assertEqual(article_key, '8104400XP24BA')
        self.assertIn('8104400XP24BA', RAPIDO_PP2_1817_QTY)
        exact, _key = extract_article('8104400XP24BA')
        self.assertEqual(exact, '8104400XP24BA')


class WarehouseReconciliationTests(TestCase):
    def setUp(self):
        self.seller = _make_seller()
        self.pp1 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP1)
        self.pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)

    def _reconcile(self, path=None, mapping=None, apply=False, warehouse=WAREHOUSE_CODE_PP2):
        return reconcile_warehouse_inventory(
            path=path,
            mapping=mapping,
            warehouse_code=warehouse,
            reference=REFERENCE,
            note=NOTE,
            apply=apply,
        )

    def test_exact_article_match(self):
        product = _make_product(self.seller, 'X0390000206')
        _open_stock(product, self.pp2, 29)
        result = self._reconcile(mapping={'X0390000206': 26}, apply=True)
        self.assertEqual(result.rows[0].status, STATUS_CHANGED)
        self.assertEqual(result.rows[0].product_id, product.pk)
        self.assertEqual(get_stock_quantity(product, self.pp2), 26)

    def test_reconciliation_decrease(self):
        product = _make_product(self.seller, 'S3010140903')
        _open_stock(product, self.pp2, 42)
        result = self._reconcile(mapping={'S3010140903': 12}, apply=True)
        movement = StockMovement.objects.get(source=SOURCE_RAPIDO)
        self.assertEqual(result.rows[0].quantity_before, 42)
        self.assertEqual(result.rows[0].quantity_after, 12)
        self.assertEqual(result.rows[0].quantity_delta, -30)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(movement.quantity_before, 42)
        self.assertEqual(movement.quantity_after, 12)
        self.assertEqual(movement.quantity_delta, -30)
        self.assertEqual(movement.reference, REFERENCE)
        self.assertEqual(movement.note, NOTE)

    def test_reconciliation_increase(self):
        product = _make_product(self.seller, 'RF059ZKR')
        _open_stock(product, self.pp2, 40)
        result = self._reconcile(mapping={'RF059ZKR': 46}, apply=True)
        movement = StockMovement.objects.get(source=SOURCE_RAPIDO)
        self.assertEqual(result.rows[0].quantity_delta, 6)
        self.assertEqual(movement.quantity_after, 46)
        self.assertEqual(get_stock_quantity(product, self.pp2), 46)

    def test_zero_qty(self):
        product = _make_product(self.seller, 'ZERO-1')
        _open_stock(product, self.pp2, 4)
        result = self._reconcile(mapping={'ZERO-1': 0}, apply=True)
        self.assertEqual(result.rows[0].status, STATUS_CHANGED)
        self.assertEqual(get_stock_quantity(product, self.pp2), 0)
        movement = StockMovement.objects.get(source=SOURCE_RAPIDO)
        self.assertEqual(movement.quantity_after, 0)
        self.assertEqual(movement.quantity_delta, -4)

    def test_unchanged_no_movement(self):
        product = _make_product(self.seller, '151000025AA')
        _open_stock(product, self.pp2, 36)
        before_moves = StockMovement.objects.count()
        result = self._reconcile(mapping={'151000025AA': 36}, apply=True)
        self.assertEqual(result.rows[0].status, STATUS_UNCHANGED)
        self.assertEqual(StockMovement.objects.count(), before_moves)
        self.assertEqual(get_stock_quantity(product, self.pp2), 36)

    def test_idempotency(self):
        product = _make_product(self.seller, 'T218107011')
        _open_stock(product, self.pp2, 34)
        self._reconcile(mapping={'T218107011': 19}, apply=True)
        moves_after_first = StockMovement.objects.filter(source=SOURCE_RAPIDO).count()
        self.assertEqual(moves_after_first, 1)
        result = self._reconcile(mapping={'T218107011': 19}, apply=True)
        self.assertEqual(result.rows[0].status, STATUS_ALREADY_APPLIED)
        self.assertEqual(
            StockMovement.objects.filter(source=SOURCE_RAPIDO).count(),
            moves_after_first,
        )
        self.assertEqual(get_stock_quantity(product, self.pp2), 19)

    def test_atomic_rollback(self):
        first = _make_product(self.seller, 'ART-ONE')
        second = _make_product(self.seller, 'ART-TWO')
        _open_stock(first, self.pp2, 10)
        _open_stock(second, self.pp2, 10)
        calls = {'n': 0}
        real = apply_stock_movement

        def boom(**kwargs):
            calls['n'] += 1
            if calls['n'] > 1:
                raise RuntimeError('forced-fail')
            return real(**kwargs)

        with patch(
            'catalog.warehouse_reconciliation.apply_stock_movement',
            side_effect=boom,
        ):
            with self.assertRaises(RuntimeError):
                self._reconcile(
                    mapping={'ART-ONE': 8, 'ART-TWO': 7},
                    apply=True,
                )
        self.assertEqual(get_stock_quantity(first, self.pp2), 10)
        self.assertEqual(get_stock_quantity(second, self.pp2), 10)
        self.assertFalse(StockMovement.objects.filter(source=SOURCE_RAPIDO).exists())

    def test_wrong_expected_warehouse(self):
        product = _make_product(self.seller, 'PP1-KEEP')
        _open_stock(product, self.pp1, 50)
        _open_stock(product, self.pp2, 9)
        with self.assertRaises(ValueError) as ctx:
            self._reconcile(mapping={'PP1-KEEP': 1}, warehouse=WAREHOUSE_CODE_PP1)
        self.assertIn('warehouse_must_be_PP2', str(ctx.exception))
        self.assertEqual(get_stock_quantity(product, self.pp1), 50)
        self.assertEqual(get_stock_quantity(product, self.pp2), 9)

    def test_no_pp1_kaspi_or_product_stock_qty_change(self):
        product = _make_product(self.seller, 'X01-90000014', stock_qty=7, price=1111)
        _open_stock(product, self.pp1, 80)
        _open_stock(product, self.pp2, 29)
        listing = ProductKaspiListing.objects.create(
            product=product,
            master_sku='sku-keep',
            merchant_sku=product.article,
            last_known_our_price=3333,
            last_known_kaspi_qty=17,
            public_url='https://kaspi.kz/shop/p/item-keep/',
        )
        extra = _make_product(self.seller, 'NOT-IN-SNAPSHOT')
        _open_stock(extra, self.pp2, 99)
        result = self._reconcile(mapping={'X01-90000014': 28}, apply=True)
        self.assertEqual(result.rows[0].status, STATUS_CHANGED)
        self.assertEqual(get_stock_quantity(product, self.pp1), 80)
        self.assertEqual(get_stock_quantity(extra, self.pp2), 99)
        product.refresh_from_db()
        listing.refresh_from_db()
        self.assertEqual(product.stock_qty, 7)
        self.assertEqual(product.price, 1111)
        self.assertEqual(listing.last_known_kaspi_qty, 17)
        self.assertEqual(listing.last_known_our_price, 3333)
        self.assertEqual(KaspiRepricerRule.objects.count(), 0)
        self.assertEqual(KaspiRepricerRecommendation.objects.count(), 0)
        self.assertEqual(KaspiOwnPriceSnapshot.objects.count(), 0)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)

    def test_unmatched_article_not_created(self):
        before = list(Product.objects.order_by('pk').values_list('pk', 'article'))
        result = self._reconcile(mapping={'UNKNOWN-ART': 4}, apply=True)
        self.assertEqual(result.rows[0].status, STATUS_UNMATCHED)
        after = list(Product.objects.order_by('pk').values_list('pk', 'article'))
        self.assertEqual(before, after)

    def test_full_rapido_snapshot_dry_run_and_apply(self):
        before_qty = rapido_pp2_before_qty()
        products = {}
        for article, qty in before_qty.items():
            product = _make_product(self.seller, article)
            products[article] = product
            _open_stock(product, self.pp2, qty)
        first = next(iter(products.values()))
        _open_stock(first, self.pp1, 4630)
        listing = ProductKaspiListing.objects.create(
            product=products['S3010140903'],
            master_sku='sku-s301',
            merchant_sku='S3010140903',
            last_known_our_price=1700,
            last_known_kaspi_qty=17,
        )
        opening_moves = StockMovement.objects.count()
        self.assertEqual(opening_moves, 63)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pp2_rapido_2026-09-18.xlsx'
            _xlsx(path, list(RAPIDO_PP2_QTY.items()))
            dry = self._reconcile(path=path, apply=False)
        summary = dry.summary
        self.assertEqual(summary['source_rows'], 62)
        self.assertEqual(summary['matched'], 62)
        self.assertEqual(summary['unmatched'], 0)
        self.assertEqual(summary['source_total'], 1898)
        self.assertEqual(summary['existing_pp2_total'], 1996)
        self.assertEqual(summary['changed'], 13)
        self.assertEqual(summary['unchanged'], 49)
        self.assertEqual(summary['net_delta'], -98)
        self.assertEqual(summary['result_total'], 1898)
        self.assertEqual(StockMovement.objects.count(), opening_moves)
        self.assertEqual(get_stock_quantity(first, self.pp1), 4630)

        applied = self._reconcile(mapping=RAPIDO_PP2_QTY, apply=True)
        self.assertEqual(applied.summary['existing_pp2_total'], 1996)
        self.assertEqual(applied.summary['net_delta'], -98)
        self.assertEqual(applied.summary['result_total'], 1898)
        self.assertEqual(applied.summary['persisted_pp2_total'], 1898)
        self.assertEqual(applied.summary['changed'], 13)
        self.assertEqual(sum(
            ProductWarehouseStock.objects.filter(warehouse=self.pp2).values_list(
                'quantity', flat=True
            )
        ), 1898)
        self.assertEqual(get_stock_quantity(first, self.pp1), 4630)
        adjustments = StockMovement.objects.filter(
            source=SOURCE_RAPIDO,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
        )
        self.assertEqual(adjustments.count(), 13)
        self.assertEqual(StockMovement.objects.count(), opening_moves + 13)
        listing.refresh_from_db()
        self.assertEqual(listing.last_known_kaspi_qty, 17)
        for product in products.values():
            product.refresh_from_db()
            self.assertEqual(product.stock_qty, 7)

        second = self._reconcile(mapping=RAPIDO_PP2_QTY, apply=True)
        self.assertEqual(second.summary['already_applied'], 13)
        self.assertEqual(second.summary['changed'], 0)
        self.assertEqual(
            StockMovement.objects.filter(source=SOURCE_RAPIDO).count(),
            13,
        )

    def test_command_dry_run_default(self):
        product = _make_product(self.seller, '4801012010')
        _open_stock(product, self.pp2, 211)
        out = StringIO()
        call_command(
            'reconcile_warehouse_inventory',
            '--snapshot',
            'rapido-2026-09-18',
            stdout=out,
        )
        self.assertIn('mode: dry-run', out.getvalue())
        self.assertEqual(get_stock_quantity(product, self.pp2), 211)
        with self.assertRaises(CommandError):
            call_command(
                'reconcile_warehouse_inventory',
                '--snapshot',
                'rapido-2026-09-18',
                '--warehouse',
                'PP1',
            )

    def test_command_apply_reporting_uses_before_total(self):
        before_qty = rapido_pp2_before_qty()
        for article, qty in before_qty.items():
            product = _make_product(self.seller, article)
            _open_stock(product, self.pp2, qty)

        dry_out = StringIO()
        call_command(
            'reconcile_warehouse_inventory',
            '--snapshot',
            'rapido-2026-09-18',
            stdout=dry_out,
        )
        dry_text = dry_out.getvalue()
        self.assertIn('existing PP2 total = 1996', dry_text)
        self.assertIn('net delta = -98', dry_text)
        self.assertIn('result total = 1898', dry_text)
        self.assertNotIn('result total = 1800', dry_text)

        apply_out = StringIO()
        call_command(
            'reconcile_warehouse_inventory',
            '--snapshot',
            'rapido-2026-09-18',
            '--apply',
            stdout=apply_out,
        )
        apply_text = apply_out.getvalue()
        self.assertIn('mode: apply', apply_text)
        self.assertIn('existing PP2 total = 1996', apply_text)
        self.assertIn('net delta = -98', apply_text)
        self.assertIn('result total = 1898', apply_text)
        self.assertIn('persisted PP2 total = 1898', apply_text)
        self.assertNotIn('result total = 1800', apply_text)
        self.assertNotIn('existing PP2 total = 1898', apply_text)
        self.assertEqual(
            sum(
                ProductWarehouseStock.objects.filter(warehouse=self.pp2).values_list(
                    'quantity', flat=True
                )
            ),
            1898,
        )

    def test_rapido_1817_dry_run_against_current_pp2(self):
        for article, qty in RAPIDO_PP2_QTY.items():
            product = _make_product(self.seller, article)
            _open_stock(product, self.pp2, qty)
        first = Product.objects.get(article='234349636')
        _open_stock(first, self.pp1, 4630)
        opening_moves = StockMovement.objects.count()

        result = reconcile_warehouse_inventory(
            mapping=RAPIDO_PP2_1817_QTY,
            warehouse_code=WAREHOUSE_CODE_PP2,
            reference=REFERENCE_1817,
            note=NOTE_1817,
            apply=False,
        )
        summary = result.summary
        self.assertEqual(summary['source_rows'], 62)
        self.assertEqual(summary['matched'], 62)
        self.assertEqual(summary['unmatched'], 0)
        self.assertEqual(summary['source_total'], 1896)
        self.assertEqual(summary['existing_pp2_total'], 1898)
        self.assertEqual(summary['changed'], 2)
        self.assertEqual(summary['unchanged'], 60)
        self.assertEqual(summary['already_applied'], 0)
        self.assertEqual(summary['net_delta'], -2)
        self.assertEqual(summary['result_total'], 1896)
        changed = [row for row in result.rows if row.status == STATUS_CHANGED]
        changed_by_article = {row.article: row for row in changed}
        self.assertEqual(set(changed_by_article), {'151000187AA', '1109140W5000'})
        self.assertEqual(changed_by_article['151000187AA'].quantity_before, 37)
        self.assertEqual(changed_by_article['151000187AA'].quantity_after, 36)
        self.assertEqual(changed_by_article['151000187AA'].quantity_delta, -1)
        self.assertEqual(changed_by_article['1109140W5000'].quantity_before, 2)
        self.assertEqual(changed_by_article['1109140W5000'].quantity_after, 1)
        self.assertEqual(changed_by_article['1109140W5000'].quantity_delta, -1)
        self.assertEqual(StockMovement.objects.count(), opening_moves)
        self.assertEqual(get_stock_quantity(first, self.pp1), 4630)

    def test_command_1817_dry_run_default(self):
        for article, qty in RAPIDO_PP2_QTY.items():
            _open_stock(_make_product(self.seller, article), self.pp2, qty)
        out = StringIO()
        call_command(
            'reconcile_warehouse_inventory',
            '--warehouse',
            'PP2',
            '--snapshot',
            'rapido-2026-09-18-1817',
            '--reference',
            'PP2-RAPIDO-2026-09-18-1817',
            stdout=out,
        )
        text = out.getvalue()
        self.assertIn('mode: dry-run', text)
        self.assertIn('reference: PP2-RAPIDO-2026-09-18-1817', text)
        self.assertIn('source rows = 62', text)
        self.assertIn('matched = 62', text)
        self.assertIn('unmatched = 0', text)
        self.assertIn('source total = 1896', text)
        self.assertIn('existing PP2 total = 1898', text)
        self.assertIn('changed = 2', text)
        self.assertIn('unchanged = 60', text)
        self.assertIn('already applied = 0', text)
        self.assertIn('net delta = -2', text)
        self.assertIn('result total = 1896', text)
        self.assertIn('151000187AA\t37 -> 36 delta=-1', text)
        self.assertIn('1109140W5000\t2 -> 1 delta=-1', text)
        self.assertEqual(
            sum(
                ProductWarehouseStock.objects.filter(warehouse=self.pp2).values_list(
                    'quantity', flat=True
                )
            ),
            1898,
        )
        self.assertFalse(
            StockMovement.objects.filter(source=SOURCE_RAPIDO).exists()
        )

    def test_rapido_product_column_extracts_cabin_filter_article(self):
        product = _make_product(self.seller, '8104400XP24BA')
        _open_stock(product, self.pp2, 20)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rapido_cabin.xlsx'
            _xlsx(
                path,
                [(20, 'Салонный фильтр 8104400XP24BA')],
                headers=('Кол-во', 'Продукт'),
            )
            result = self._reconcile(path=path, apply=False)
        self.assertEqual(result.rows[0].article, '8104400XP24BA')
        self.assertEqual(result.rows[0].status, STATUS_UNCHANGED)
        self.assertEqual(result.rows[0].product_id, product.pk)


class KaspiStockPreviewTests(TestCase):
    def setUp(self):
        self.seller = _make_seller('kaspi-preview-seller')
        self.pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)

    def test_ready_hold_and_no_listing(self):
        single = _make_product(self.seller, 'S3010140903')
        dup = _make_product(self.seller, '1064000180')
        none = _make_product(self.seller, 'F081109111HD')
        _open_stock(single, self.pp2, 12)
        _open_stock(dup, self.pp2, 15)
        _open_stock(none, self.pp2, 26)
        ProductKaspiListing.objects.create(
            product=single,
            master_sku='sku-single',
            merchant_sku='S3010140903',
            last_known_our_price=4400,
            last_known_kaspi_qty=17,
        )
        ProductKaspiListing.objects.create(
            product=dup,
            master_sku='sku-dup-a',
            merchant_sku='1064000180-a',
            last_known_our_price=1000,
            last_known_kaspi_qty=8,
        )
        ProductKaspiListing.objects.create(
            product=dup,
            master_sku='sku-dup-b',
            merchant_sku='1064000180-b',
            last_known_our_price=1000,
            last_known_kaspi_qty=7,
        )
        subset = {
            'S3010140903': 12,
            '1064000180': 15,
            'F081109111HD': 26,
        }
        result = build_preview_from_listings(articles=subset, use_live_pp2=True)
        by_article = {}
        for row in result.rows:
            by_article.setdefault(row.article, []).append(row)
        self.assertEqual(by_article['S3010140903'][0].status, STATUS_READY)
        self.assertEqual(by_article['S3010140903'][0].pp2_target, 12)
        self.assertEqual(by_article['S3010140903'][0].delta, -5)
        self.assertEqual(len(by_article['1064000180']), 2)
        self.assertTrue(
            all(row.status == STATUS_HOLD_DUPLICATE for row in by_article['1064000180'])
        )
        self.assertTrue(
            all(row.pp2_target is None for row in by_article['1064000180'])
        )
        self.assertEqual(by_article['F081109111HD'][0].status, STATUS_NO_LISTING)
        with TemporaryDirectory() as tmp:
            path = write_kaspi_stock_preview_xlsx(
                result, Path(tmp) / 'preview.xlsx'
            )
            self.assertTrue(path.exists())
        self.assertFalse(result.upload_ready)
