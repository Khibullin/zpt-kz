from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from openpyxl import Workbook

from catalog.models import (
    Product,
    ProductWarehouseStock,
    SellerProfile,
    StockMovement,
    Warehouse,
)
from catalog.stock_service import (
    InsufficientStockError,
    StockServiceError,
    apply_stock_movement,
    get_kaspi_available_stock,
    get_stock_quantity,
    set_stock_quantity,
    transfer_stock,
)
from catalog.warehouse_stock_sync import (
    STATUS_DUPLICATE_ARTICLE,
    STATUS_INVALID_QUANTITY,
    STATUS_MATCHED_NO_CHANGE,
    STATUS_MISSING_PRODUCT,
    STATUS_WOULD_ADJUST,
    STATUS_WOULD_CREATE_BALANCE,
    parse_stock_quantity,
    sync_warehouse_stocks,
)
from catalog.warehouses import (
    KASPI_AVAILABLE_WAREHOUSE_CODE,
    WAREHOUSE_CODE_PP1,
    WAREHOUSE_CODE_PP2,
)


def _xlsx(path, headers, rows, sheet='Sheet'):
    workbook = Workbook()
    sheet_obj = workbook.active
    sheet_obj.title = sheet
    sheet_obj.append(headers)
    for row in rows:
        sheet_obj.append(row)
    workbook.save(path)


def _make_seller(username, name, phone):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
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


class WarehouseSeedTests(TestCase):
    def test_pp1_and_pp2_exist_after_migrate(self):
        self.assertTrue(Warehouse.objects.filter(code=WAREHOUSE_CODE_PP1).exists())
        self.assertTrue(Warehouse.objects.filter(code=WAREHOUSE_CODE_PP2).exists())
        pp1 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP1)
        pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)
        self.assertEqual(pp1.name, 'Основной склад')
        self.assertEqual(pp2.name, 'Fulfillment')
        self.assertTrue(pp1.is_active)
        self.assertTrue(pp2.is_active)


class ProductWarehouseStockConstraintTests(TestCase):
    def setUp(self):
        self.seller = _make_seller('wh-owner', 'AG Parts WH', '77011112233')
        self.product = _make_product(self.seller, 'ART-1')
        self.pp1 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP1)

    def test_unique_product_warehouse(self):
        ProductWarehouseStock.objects.create(
            product=self.product,
            warehouse=self.pp1,
            quantity=1,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductWarehouseStock.objects.create(
                    product=self.product,
                    warehouse=self.pp1,
                    quantity=2,
                )


class StockMovementServiceTests(TestCase):
    def setUp(self):
        self.seller = _make_seller('wh-move', 'AG Parts Move', '77011112234')
        self.product = _make_product(self.seller, 'ART-MOVE')
        self.pp1 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP1)
        self.pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)

    def test_opening_balance(self):
        movement = apply_stock_movement(
            product=self.product,
            warehouse=self.pp1,
            movement_type=StockMovement.MovementType.OPENING,
            quantity_delta=10,
            source='test',
        )
        self.assertEqual(movement.quantity_before, 0)
        self.assertEqual(movement.quantity_after, 10)
        self.assertEqual(get_stock_quantity(self.product, self.pp1), 10)

    def test_receipt_increases_quantity(self):
        apply_stock_movement(
            product=self.product,
            warehouse=self.pp1,
            movement_type=StockMovement.MovementType.OPENING,
            quantity_delta=4,
            source='test',
        )
        apply_stock_movement(
            product=self.product,
            warehouse=self.pp1,
            movement_type=StockMovement.MovementType.RECEIPT,
            quantity_delta=6,
            source='test',
        )
        self.assertEqual(get_stock_quantity(self.product, self.pp1), 10)

    def test_sale_decreases_quantity(self):
        apply_stock_movement(
            product=self.product,
            warehouse=self.pp2,
            movement_type=StockMovement.MovementType.RECEIPT,
            quantity_delta=5,
            source='test',
        )
        apply_stock_movement(
            product=self.product,
            warehouse=self.pp2,
            movement_type=StockMovement.MovementType.SALE,
            quantity_delta=-2,
            source='test',
        )
        self.assertEqual(get_stock_quantity(self.product, self.pp2), 3)

    def test_negative_balance_rejected(self):
        apply_stock_movement(
            product=self.product,
            warehouse=self.pp2,
            movement_type=StockMovement.MovementType.RECEIPT,
            quantity_delta=1,
            source='test',
        )
        with self.assertRaises(InsufficientStockError):
            apply_stock_movement(
                product=self.product,
                warehouse=self.pp2,
                movement_type=StockMovement.MovementType.SALE,
                quantity_delta=-5,
                source='test',
            )
        self.assertEqual(get_stock_quantity(self.product, self.pp2), 1)
        self.assertEqual(StockMovement.objects.filter(movement_type='SALE').count(), 0)

    def test_first_set_creates_opening(self):
        movement = set_stock_quantity(
            product=self.product,
            warehouse=self.pp1,
            new_quantity=12,
            source='inventory',
        )
        self.assertIsNotNone(movement)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.OPENING)
        self.assertEqual(movement.quantity_before, 0)
        self.assertEqual(movement.quantity_after, 12)
        self.assertEqual(movement.quantity_delta, 12)
        self.assertEqual(get_stock_quantity(self.product, self.pp1), 12)

    def test_opening_zero_initializes_balance(self):
        movement = set_stock_quantity(
            product=self.product,
            warehouse=self.pp1,
            new_quantity=0,
            source='inventory',
        )
        self.assertIsNotNone(movement)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.OPENING)
        self.assertEqual(movement.quantity_before, 0)
        self.assertEqual(movement.quantity_after, 0)
        self.assertEqual(movement.quantity_delta, 0)
        stock = ProductWarehouseStock.objects.get(
            product=self.product,
            warehouse=self.pp1,
        )
        self.assertEqual(stock.quantity, 0)

    def test_later_set_creates_adjustment(self):
        set_stock_quantity(
            product=self.product,
            warehouse=self.pp1,
            new_quantity=12,
            source='inventory',
        )
        movement = set_stock_quantity(
            product=self.product,
            warehouse=self.pp1,
            new_quantity=15,
            source='inventory',
        )
        self.assertEqual(movement.movement_type, StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(movement.quantity_before, 12)
        self.assertEqual(movement.quantity_after, 15)
        self.assertEqual(movement.quantity_delta, 3)

    def test_adjustment_same_qty_does_not_write(self):
        set_stock_quantity(
            product=self.product,
            warehouse=self.pp1,
            new_quantity=8,
            source='inventory',
        )
        before_stock = ProductWarehouseStock.objects.count()
        before_move = StockMovement.objects.count()
        result = set_stock_quantity(
            product=self.product,
            warehouse=self.pp1,
            new_quantity=8,
            source='inventory',
        )
        self.assertIsNone(result)
        self.assertEqual(ProductWarehouseStock.objects.count(), before_stock)
        self.assertEqual(StockMovement.objects.count(), before_move)

    def test_transfer_pp1_to_pp2(self):
        set_stock_quantity(
            product=self.product,
            warehouse=self.pp1,
            new_quantity=10,
            source='test',
        )
        ref = transfer_stock(
            product=self.product,
            from_warehouse=self.pp1,
            to_warehouse=self.pp2,
            quantity=4,
            source='fulfillment',
        )
        self.assertEqual(get_stock_quantity(self.product, self.pp1), 6)
        self.assertEqual(get_stock_quantity(self.product, self.pp2), 4)
        moves = StockMovement.objects.filter(reference=ref).order_by('id')
        self.assertEqual(moves.count(), 2)
        self.assertEqual(moves[0].movement_type, StockMovement.MovementType.TRANSFER_OUT)
        self.assertEqual(moves[1].movement_type, StockMovement.MovementType.TRANSFER_IN)

    def test_transfer_rolls_back_when_pp1_short(self):
        set_stock_quantity(
            product=self.product,
            warehouse=self.pp1,
            new_quantity=2,
            source='test',
        )
        with self.assertRaises(InsufficientStockError):
            transfer_stock(
                product=self.product,
                from_warehouse=self.pp1,
                to_warehouse=self.pp2,
                quantity=5,
                source='fulfillment',
            )
        self.assertEqual(get_stock_quantity(self.product, self.pp1), 2)
        self.assertEqual(get_stock_quantity(self.product, self.pp2), 0)
        self.assertFalse(
            StockMovement.objects.filter(movement_type='TRANSFER_IN').exists()
        )

    def test_kaspi_available_is_pp2_only(self):
        set_stock_quantity(
            product=self.product,
            warehouse=self.pp1,
            new_quantity=20,
            source='test',
        )
        set_stock_quantity(
            product=self.product,
            warehouse=self.pp2,
            new_quantity=3,
            source='test',
        )
        self.assertEqual(KASPI_AVAILABLE_WAREHOUSE_CODE, WAREHOUSE_CODE_PP2)
        self.assertEqual(get_kaspi_available_stock(self.product), 3)
        self.assertNotEqual(
            get_kaspi_available_stock(self.product),
            get_stock_quantity(self.product, self.pp1)
            + get_stock_quantity(self.product, self.pp2),
        )

    def test_missing_pp2_balance_is_zero(self):
        self.assertEqual(get_kaspi_available_stock(self.product), 0)

    def test_opening_second_time_rejected(self):
        apply_stock_movement(
            product=self.product,
            warehouse=self.pp1,
            movement_type=StockMovement.MovementType.OPENING,
            quantity_delta=1,
            source='test',
        )
        with self.assertRaises(StockServiceError):
            apply_stock_movement(
                product=self.product,
                warehouse=self.pp1,
                movement_type=StockMovement.MovementType.OPENING,
                quantity_delta=1,
                source='test',
            )


class ParseStockQuantityTests(TestCase):
    def test_rejects_non_numeric(self):
        for raw in ('no', 'yes', 'preorder', '', None, '3.5', -1, True):
            qty, error = parse_stock_quantity(raw)
            self.assertIsNone(qty)
            self.assertTrue(error)

    def test_accepts_int_and_int_float(self):
        self.assertEqual(parse_stock_quantity(0), (0, ''))
        self.assertEqual(parse_stock_quantity(37), (37, ''))
        self.assertEqual(parse_stock_quantity(12.0), (12, ''))
        self.assertEqual(parse_stock_quantity('8'), (8, ''))


class WarehouseStockImportTests(TestCase):
    def setUp(self):
        self.seller = _make_seller('wh-imp', 'AG Parts Imp', '77011112235')
        self.other = _make_seller('wh-other', 'Other Seller', '77011112236')
        self.product = _make_product(self.seller, '4801012010', stock_qty=27)
        self.other_product = _make_product(self.other, '4801012010', stock_qty=9)
        self.pp1 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP1)
        self.pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)

    def _sync(self, path, apply=False, **kwargs):
        return sync_warehouse_stocks(
            path=path,
            seller=self.seller,
            apply=apply,
            confirm_pp_source=True,
            **kwargs,
        )

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['4801012010', 5, 9]])
            stocks_before = ProductWarehouseStock.objects.count()
            moves_before = StockMovement.objects.count()
            result = self._sync(path)
            self.assertEqual(result.rows[0].status, STATUS_WOULD_CREATE_BALANCE)
            self.assertEqual(ProductWarehouseStock.objects.count(), stocks_before)
            self.assertEqual(StockMovement.objects.count(), moves_before)
            self.product.refresh_from_db()
            self.assertEqual(self.product.stock_qty, 27)

    def test_apply_creates_balances(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['4801012010', 5, 9]])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_WOULD_CREATE_BALANCE)
            self.assertEqual(get_stock_quantity(self.product, self.pp1), 5)
            self.assertEqual(get_stock_quantity(self.product, self.pp2), 9)
            self.assertEqual(
                StockMovement.objects.filter(
                    movement_type=StockMovement.MovementType.OPENING
                ).count(),
                2,
            )
            self.assertFalse(
                StockMovement.objects.filter(
                    movement_type=StockMovement.MovementType.ADJUSTMENT
                ).exists()
            )
            self.product.refresh_from_db()
            self.assertEqual(self.product.stock_qty, 27)

    def test_repeat_same_qty_is_no_change(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['4801012010', 5, 9]])
            self._sync(path, apply=True)
            moves_before = StockMovement.objects.count()
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_MATCHED_NO_CHANGE)
            self.assertEqual(StockMovement.objects.count(), moves_before)

    def test_changed_qty_creates_adjustment(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['4801012010', 5, 9]])
            self._sync(path, apply=True)
            path2 = Path(tmp) / 'stock2.xlsx'
            _xlsx(path2, ['article', 'PP1', 'PP2'], [['4801012010', 5, 11]])
            result = self._sync(path2, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_WOULD_ADJUST)
            self.assertEqual(get_stock_quantity(self.product, self.pp2), 11)
            self.assertEqual(result.rows[0].pp2.old_qty, 9)
            self.assertEqual(result.rows[0].pp2.delta, 2)

    def test_other_seller_article_is_missing(self):
        _make_product(self.other, 'ONLY-OTHER')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['ONLY-OTHER', 4, 4]])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_MISSING_PRODUCT)
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)

    def test_same_article_on_other_seller_is_not_used(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['4801012010', 1, 1]])
            result = sync_warehouse_stocks(
                path=path,
                seller=self.other,
                apply=True,
                confirm_pp_source=True,
            )
            row = result.rows[0]
            self.assertEqual(row.product_id, self.other_product.pk)
            self.assertNotEqual(row.product_id, self.product.pk)
            self.assertEqual(get_stock_quantity(self.product, self.pp2), 0)
            self.assertEqual(get_stock_quantity(self.other_product, self.pp2), 1)

    def test_duplicate_article_not_applied(self):
        extra = _make_product(self.seller, 'DUP-1')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(
                path,
                ['article', 'PP1', 'PP2'],
                [['DUP-1', 3, 4], ['DUP-1', 8, 9]],
            )
            result = self._sync(path, apply=True)
            self.assertTrue(
                all(row.status == STATUS_DUPLICATE_ARTICLE for row in result.rows)
            )
            self.assertEqual(get_stock_quantity(extra, self.pp1), 0)

    def test_invalid_quantity_not_applied(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['4801012010', 'no', 9]])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_INVALID_QUANTITY)
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)

    def test_importer_does_not_change_stock_qty(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['4801012010', 4, 6]])
            self._sync(path, apply=True)
            self.product.refresh_from_db()
            self.assertEqual(self.product.stock_qty, 27)
            self.assertEqual(self.product.price, 1000)
            self.assertEqual(self.product.cost_price, 400)
            self.assertFalse(self.product.publish_to_kaspi)

    def test_missing_product(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['UNKNOWN-ART', 1, 1]])
            result = self._sync(path)
            self.assertEqual(result.rows[0].status, STATUS_MISSING_PRODUCT)

    def test_single_warehouse_code_pp2(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wms.xlsx'
            _xlsx(path, ['Продукт', 'Кол-во'], [['4801012010', 31]])
            result = self._sync(
                path,
                apply=True,
                warehouse_code=WAREHOUSE_CODE_PP2,
                quantity_column='Кол-во',
            )
            self.assertEqual(result.rows[0].status, STATUS_WOULD_CREATE_BALANCE)
            self.assertEqual(get_stock_quantity(self.product, self.pp2), 31)
            self.assertEqual(get_stock_quantity(self.product, self.pp1), 0)
            movement = StockMovement.objects.get(warehouse=self.pp2)
            self.assertEqual(movement.movement_type, StockMovement.MovementType.OPENING)

    def test_apply_opening_zero_creates_balance(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wms.xlsx'
            _xlsx(path, ['Продукт', 'Кол-во'], [['4801012010', 0]])
            result = self._sync(
                path,
                apply=True,
                warehouse_code=WAREHOUSE_CODE_PP2,
                quantity_column='Кол-во',
            )
            self.assertEqual(result.rows[0].status, STATUS_WOULD_CREATE_BALANCE)
            stock = ProductWarehouseStock.objects.get(
                product=self.product,
                warehouse=self.pp2,
            )
            self.assertEqual(stock.quantity, 0)
            movement = StockMovement.objects.get()
            self.assertEqual(movement.movement_type, StockMovement.MovementType.OPENING)
            self.assertEqual(movement.quantity_delta, 0)
            self.assertEqual(movement.quantity_before, 0)
            self.assertEqual(movement.quantity_after, 0)

    def test_apply_without_confirm_pp_source_fails(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['4801012010', 1, 1]])
            with self.assertRaises(ValueError):
                sync_warehouse_stocks(
                    path=path,
                    seller=self.seller,
                    apply=True,
                    confirm_pp_source=False,
                )

    def test_pp2_apply_without_confirm_fails(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wms.xlsx'
            _xlsx(path, ['Продукт', 'Кол-во'], [['4801012010', 31]])
            with self.assertRaises(ValueError) as ctx:
                sync_warehouse_stocks(
                    path=path,
                    seller=self.seller,
                    apply=True,
                    warehouse_code=WAREHOUSE_CODE_PP2,
                    quantity_column='Кол-во',
                    confirm_pp_source=False,
                )
            self.assertIn('confirm-pp-source', str(ctx.exception))
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)
            self.assertEqual(StockMovement.objects.count(), 0)

    def test_pp2_apply_with_confirm_allowed(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wms.xlsx'
            _xlsx(path, ['Продукт', 'Кол-во'], [['4801012010', 31]])
            result = sync_warehouse_stocks(
                path=path,
                seller=self.seller,
                apply=True,
                warehouse_code=WAREHOUSE_CODE_PP2,
                quantity_column='Кол-во',
                confirm_pp_source=True,
            )
            self.assertEqual(result.rows[0].status, STATUS_WOULD_CREATE_BALANCE)
            self.assertEqual(get_stock_quantity(self.product, self.pp2), 31)

    def test_pp2_dry_run_without_confirm_allowed(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wms.xlsx'
            _xlsx(path, ['Продукт', 'Кол-во'], [['4801012010', 31]])
            result = sync_warehouse_stocks(
                path=path,
                seller=self.seller,
                apply=False,
                warehouse_code=WAREHOUSE_CODE_PP2,
                quantity_column='Кол-во',
                confirm_pp_source=False,
            )
            self.assertEqual(result.rows[0].status, STATUS_WOULD_CREATE_BALANCE)
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)
            self.assertEqual(StockMovement.objects.count(), 0)

    def test_management_command_dry_run(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stock.xlsx'
            _xlsx(path, ['article', 'PP1', 'PP2'], [['4801012010', 2, 3]])
            out = StringIO()
            call_command(
                'sync_warehouse_stocks',
                str(path),
                '--seller-profile-id',
                str(self.seller.pk),
                stdout=out,
            )
            self.assertIn('mode: dry-run', out.getvalue())
            self.assertEqual(ProductWarehouseStock.objects.count(), 0)
            self.product.refresh_from_db()
            self.assertEqual(self.product.stock_qty, 27)
