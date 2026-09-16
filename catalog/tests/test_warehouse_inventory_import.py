from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from catalog.models import (
    Product,
    ProductKaspiListing,
    ProductWarehouseStock,
    SellerProfile,
    StockMovement,
    Warehouse,
)
from catalog.stock_service import apply_stock_movement, get_stock_quantity
from catalog.warehouse_inventory_import import (
    SOURCE_INVENTORY,
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
from repricer.models import (
    KaspiCompetitorOfferSnapshot,
    KaspiOwnPriceSnapshot,
    KaspiRepricerRecommendation,
    KaspiRepricerRule,
)


HEADERS = ['Артикул', 'Наличие на складе АГ']
INVENTORY_DATE = date(2026, 9, 7)


def _xlsx(path, rows, headers=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Инв'
    sheet.append(headers or HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _make_seller(username='inv-seller'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=username,
        phone='77011112240',
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
        'publish_to_kaspi': False,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class WarehouseInventoryImportTests(TestCase):
    def setUp(self):
        self.seller = _make_seller()
        self.product = _make_product(self.seller, 'X0390000206')
        self.pp1 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP1)
        self.pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)

    def _import(self, path, apply=False, warehouse=WAREHOUSE_CODE_PP1, **kwargs):
        return import_warehouse_inventory(
            path=path,
            warehouse_code=warehouse,
            inventory_date=INVENTORY_DATE,
            apply=apply,
            **kwargs,
        )

    def test_exact_article_match(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 12]])
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_CREATE_OPENING)
            self.assertEqual(result.rows[0].product_id, self.product.pk)
            self.assertEqual(get_stock_quantity(self.product, self.pp1), 12)

    def test_unmatched_article_skipped(self):
        similar = _make_product(self.seller, '1056025900')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['1056022300', 92]])
            products_before = Product.objects.count()
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_UNMATCHED)
            self.assertEqual(Product.objects.count(), products_before)
            self.assertEqual(get_stock_quantity(similar, self.pp1), 0)
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)

    def test_blank_qty_is_not_zero(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', None]])
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_BLANK_QTY_MATCHED)
            self.assertIsNone(result.rows[0].qty)
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)
            self.assertEqual(StockMovement.objects.count(), 0)

    def test_qty_zero_is_real_zero(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 0]])
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_CREATE_OPENING)
            stock = ProductWarehouseStock.objects.get(
                product=self.product, warehouse=self.pp1
            )
            self.assertEqual(stock.quantity, 0)
            movement = StockMovement.objects.get()
            self.assertEqual(movement.movement_type, StockMovement.MovementType.OPENING)
            self.assertEqual(movement.quantity_before, 0)
            self.assertEqual(movement.quantity_after, 0)
            self.assertEqual(movement.quantity_delta, 0)

    def test_negative_qty_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', -3]])
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_ERROR)
            self.assertEqual(result.rows[0].reason, 'negative')
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)

    def test_fractional_qty_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 1.5]])
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_ERROR)
            self.assertIn(result.rows[0].reason, {'not_integer', 'non_numeric'})
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)

    def test_invalid_text_qty_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 'нет']])
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_ERROR)
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)

    def test_dry_run_zero_db_writes(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 12]])
            stocks_before = ProductWarehouseStock.objects.count()
            moves_before = StockMovement.objects.count()
            result = self._import(path, apply=False)
            self.assertEqual(result.rows[0].status, STATUS_CREATE_OPENING)
            self.assertFalse(result.apply)
            self.assertEqual(ProductWarehouseStock.objects.count(), stocks_before)
            self.assertEqual(StockMovement.objects.count(), moves_before)
            self.product.refresh_from_db()
            self.assertEqual(self.product.stock_qty, 7)

    def test_apply_creates_balance_and_opening_movement(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 15]])
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_CREATE_OPENING)
            stock = ProductWarehouseStock.objects.get(
                product=self.product, warehouse=self.pp1
            )
            self.assertEqual(stock.quantity, 15)
            movements = list(StockMovement.objects.all())
            self.assertEqual(len(movements), 1)
            movement = movements[0]
            self.assertEqual(movement.movement_type, StockMovement.MovementType.OPENING)
            self.assertEqual(movement.quantity_before, 0)
            self.assertEqual(movement.quantity_after, 15)
            self.assertEqual(movement.quantity_delta, 15)
            self.assertEqual(movement.source, SOURCE_INVENTORY)
            self.assertEqual(movement.reference, 'PP1-2026-09-07')
            self.assertIn('Physical inventory as of 2026-09-07', movement.note)
            self.assertIn('inv.xlsx', movement.note)
            self.assertNotEqual(movement.created_at.date(), INVENTORY_DATE)

    def test_pp2_untouched(self):
        apply_stock_movement(
            product=self.product,
            warehouse=self.pp2,
            movement_type=StockMovement.MovementType.OPENING,
            quantity_delta=40,
            source='seed-pp2',
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 8]])
            self._import(path, apply=True)
            self.assertEqual(get_stock_quantity(self.product, self.pp1), 8)
            self.assertEqual(get_stock_quantity(self.product, self.pp2), 40)
            self.assertEqual(
                StockMovement.objects.filter(warehouse=self.pp2).count(),
                1,
            )

    def test_existing_pp1_balance_is_conflict(self):
        apply_stock_movement(
            product=self.product,
            warehouse=self.pp1,
            movement_type=StockMovement.MovementType.OPENING,
            quantity_delta=3,
            source='manual',
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 99]])
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_CONFLICT_EXISTING_BALANCE)
            self.assertEqual(get_stock_quantity(self.product, self.pp1), 3)
            self.assertEqual(StockMovement.objects.count(), 1)

    def test_duplicate_same_reference_is_already_imported(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 11]])
            self._import(path, apply=True)
            moves_before = StockMovement.objects.count()
            result = self._import(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_ALREADY_IMPORTED)
            self.assertEqual(StockMovement.objects.count(), moves_before)
            self.assertEqual(get_stock_quantity(self.product, self.pp1), 11)

    def test_unknown_warehouse_fails(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 1]])
            with self.assertRaises(ValueError) as ctx:
                self._import(path, warehouse='PP9')
            self.assertIn('warehouse_not_found', str(ctx.exception))

    def test_duplicate_source_article_reported(self):
        extra = _make_product(self.seller, 'DUP-1')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['DUP-1', 3], ['DUP-1', 8]])
            result = self._import(path, apply=True)
            self.assertTrue(
                all(row.status == STATUS_DUPLICATE_ARTICLE for row in result.rows)
            )
            self.assertEqual(get_stock_quantity(extra, self.pp1), 0)
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)

    def test_transaction_rollback_on_system_error(self):
        second = _make_product(self.seller, 'ART-TWO')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 5], ['ART-TWO', 9]])
            calls = {'n': 0}
            real = apply_stock_movement

            def boom(**kwargs):
                calls['n'] += 1
                if calls['n'] > 1:
                    raise RuntimeError('forced-fail')
                return real(**kwargs)

            with patch(
                'catalog.warehouse_inventory_import.apply_stock_movement',
                side_effect=boom,
            ):
                with self.assertRaises(RuntimeError):
                    self._import(path, apply=True)
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)
            self.assertEqual(StockMovement.objects.count(), 0)
            self.assertEqual(get_stock_quantity(self.product, self.pp1), 0)
            self.assertEqual(get_stock_quantity(second, self.pp1), 0)

    def test_no_product_auto_create(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['UNKNOWN-ART', 4]])
            before = list(Product.objects.order_by('pk').values_list('pk', 'article'))
            self._import(path, apply=True)
            after = list(Product.objects.order_by('pk').values_list('pk', 'article'))
            self.assertEqual(before, after)

    def test_kaspi_listing_and_repricer_untouched(self):
        listing = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku='115801437_271928151',
            merchant_sku=self.product.article,
            last_known_our_price=3034,
            last_known_kaspi_qty=2,
            public_url='https://kaspi.kz/shop/p/item-1/',
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 6]])
            self._import(path, apply=True)
        listing.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(listing.last_known_our_price, 3034)
        self.assertEqual(listing.last_known_kaspi_qty, 2)
        self.assertEqual(listing.public_url, 'https://kaspi.kz/shop/p/item-1/')
        self.assertEqual(self.product.stock_qty, 7)
        self.assertEqual(self.product.price, 1000)
        self.assertEqual(self.product.cost_price, 400)
        self.assertEqual(self.product.status, 'active')
        self.assertEqual(KaspiRepricerRule.objects.count(), 0)
        self.assertEqual(KaspiRepricerRecommendation.objects.count(), 0)
        self.assertEqual(KaspiOwnPriceSnapshot.objects.count(), 0)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)

    def test_command_dry_run_default_and_unknown_warehouse(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inv.xlsx'
            _xlsx(path, [['X0390000206', 12]])
            out = StringIO()
            call_command(
                'import_warehouse_inventory',
                '--warehouse',
                'PP1',
                '--file',
                str(path),
                '--inventory-date',
                '2026-09-07',
                stdout=out,
            )
            self.assertIn('mode: dry-run', out.getvalue())
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)
            with self.assertRaises(CommandError):
                call_command(
                    'import_warehouse_inventory',
                    '--warehouse',
                    'NOPE',
                    '--file',
                    str(path),
                    '--inventory-date',
                    '2026-09-07',
                )
