import csv
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from openpyxl import Workbook

from catalog.kaspi_sales_import import (
    MATCH_AMBIGUOUS,
    MATCH_LISTING,
    MATCH_PRODUCT,
    MATCH_UNMATCHED,
    OP_PURCHASE,
    OP_RETURN,
    STATUS_ALREADY_IMPORTED,
    STATUS_CREATED,
    STATUS_INVALID,
    STATUS_WOULD_CREATE,
    parse_details_quantity,
    parse_kaspi_decimal,
    operation_fingerprint,
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
    Warehouse,
)
from catalog.stock_service import set_stock_quantity
from catalog.warehouses import WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2
from orders.models import Order


SALES_HEADERS = [
    'Адрес точки продаж',
    'Идентификатор точки',
    'ID терминала',
    'Бренд',
    '№ документа Покупки/Возврата',
    'Номер заказа (ID/RRN)',
    'Дата операции',
    'Время',
    'Дата учета операции',
    'Тип операции',
    'Тип оплаты',
    'Тип оплаты 2',
    'Сумма операции (т)',
    'Сумма к зачислению/ списанию (т)',
    'Комиссия за операции (т)',
    'Комиссия за операции (т) без ндс',
    'Комиссия за операции (%) без ндс',
    'Комиссия за операции по карте (т)',
    'Комиссия за операции по карте (%)',
    'Комиссия за обеспечение платежа (т)',
    'Комиссия за обеспечение платежа (%)',
    'Комиссия Kaspi Pay (т)',
    'Комиссия Kaspi Pay (%)',
    'Комиссия Kaspi Travel (т)',
    'Комиссия Kaspi Travel (%)',
    'Оплата услуг за акцию Бонусы на товар',
    'Оплата услуг за акцию Бонусы за отзыв',
    '№ документа списания стоимости за Kaspi Доставку',
    'Стоимость услуги за Kaspi Доставку',
    'Срок для Кредита на Покупки',
    'Детали покупки',
]

METADATA_ROWS = [
    ['Период', '01.08.2026 - 31.08.2026'],
    ['ИИН/БИН', '123456789012'],
    ['Наименование', 'AG Parts'],
    [],
]


def _sales_row(**overrides):
    data = {header: '' for header in SALES_HEADERS}
    data.update({
        'Идентификатор точки': 'PP2',
        'ID терминала': 'T-1',
        '№ документа Покупки/Возврата': 'DOC-1',
        'Номер заказа (ID/RRN)': 'RRN-100',
        'Дата операции': '31.08.2026',
        'Время': '16:32:54',
        'Дата учета операции': '31.08.2026',
        'Тип операции': 'Покупка',
        'Тип оплаты': 'Kaspi Gold',
        'Сумма операции (т)': '1 700,00',
        'Сумма к зачислению/ списанию (т)': '1 503,79',
        'Комиссия за операции (т)': '196,21',
        'Стоимость услуги за Kaspi Доставку': '0',
        'Детали покупки': 'Chery воздушный фильтр T151109111',
    })
    data.update(overrides)
    return [data[header] for header in SALES_HEADERS]


def _write_csv(path, rows, headers=SALES_HEADERS, metadata=METADATA_ROWS):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        for meta in metadata:
            writer.writerow(meta)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)


def _write_xlsx(path, rows, headers=SALES_HEADERS, metadata=METADATA_ROWS, sheet='Sales'):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    for meta in metadata:
        worksheet.append(meta if meta else [None])
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
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
        'price': 1700,
        'cost_price': 400,
        'stock_qty': 9,
        'publish_to_kaspi': False,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _local_stamp(value):
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S')


def _snapshot(seller):
    return {
        'stocks': list(
            ProductWarehouseStock.objects.order_by('pk').values_list('pk', 'quantity')
        ),
        'moves': list(
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
        'site_orders': Order.objects.count(),
    }


class SyncKaspiSalesReportTests(TestCase):
    def setUp(self):
        self.seller = _make_seller('sales-own', 'AG Parts', '77771110001')
        self.other = _make_seller('sales-other', 'Other Shop', '77771110002')
        self.product = _make_product(self.seller, 'T151109111', price=1700, stock_qty=9)
        self.listing_synced_at = timezone.make_aware(datetime(2026, 9, 1, 12, 0, 0))
        self.listing = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku='100500',
            merchant_sku='T151109111',
            last_known_our_price=4500,
            last_known_kaspi_qty=25,
            last_synced_at=self.listing_synced_at,
        )
        self.pp1 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP1)
        self.pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)
        set_stock_quantity(product=self.product, warehouse=self.pp1, new_quantity=3, source='test')
        set_stock_quantity(product=self.product, warehouse=self.pp2, new_quantity=25, source='test')

    def _sync(self, path, apply=False, **kwargs):
        return sync_kaspi_sales_report(
            path=path,
            seller=self.seller,
            apply=apply,
            **kwargs,
        )

    def _assert_stock_untouched(self, before):
        after = _snapshot(self.seller)
        self.assertEqual(after['stocks'], before['stocks'])
        self.assertEqual(after['moves'], before['moves'])
        self.assertEqual(after['products'], before['products'])
        self.assertEqual(after['listings'], before['listings'])
        self.assertEqual(after['site_orders'], before['site_orders'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, 1700)
        self.assertEqual(self.product.cost_price, 400)
        self.assertEqual(self.product.stock_qty, 9)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.last_known_our_price, 4500)
        self.assertEqual(self.listing.last_known_kaspi_qty, 25)
        self.assertEqual(self.listing.last_synced_at, self.listing_synced_at)

    def test_header_found_after_metadata_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            result = self._sync(path)
            self.assertEqual(result.header_row, 5)
            self.assertEqual(result.rows[0].status, STATUS_WOULD_CREATE)

    def test_csv_purchase_parse(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            result = self._sync(path)
            row = result.rows[0]
            self.assertEqual(row.operation_type, OP_PURCHASE)
            self.assertEqual(row.external_order_id, 'RRN-100')
            self.assertEqual(row.gross_amount, Decimal('1700.00'))
            self.assertEqual(row.quantity, 1)
            self.assertTrue(timezone.is_aware(row.operation_at))
            self.assertEqual(_local_stamp(row.operation_at), '2026-08-31 16:32:54')

    def test_xlsx_purchase_parse(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.xlsx'
            _write_xlsx(path, [_sales_row()])
            result = self._sync(path)
            row = result.rows[0]
            self.assertEqual(row.operation_type, OP_PURCHASE)
            self.assertEqual(row.gross_amount, Decimal('1700.00'))
            self.assertEqual(row.match_status, MATCH_LISTING)
            self.assertEqual(result.header_row, 5)

    def test_quantity_defaults_to_one_without_pcs_suffix(self):
        self.assertEqual(parse_details_quantity('Масляный фильтр 4801012010'), 1)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'Детали покупки': 'Масляный фильтр 4801012010',
                'Номер заказа (ID/RRN)': 'RRN-Q1',
            })])
            row = self._sync(path).rows[0]
            self.assertEqual(row.quantity, 1)

    def test_quantity_from_pcs_suffix(self):
        self.assertEqual(
            parse_details_quantity('Chery салонный фильтр T218107011, 4 шт.'),
            4,
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'Детали покупки': 'Chery салонный фильтр T218107011, 4 шт.',
                'Сумма операции (т)': '6 600,00',
                'Номер заказа (ID/RRN)': 'RRN-Q4',
            })])
            row = self._sync(path).rows[0]
            self.assertEqual(row.quantity, 4)

    def test_return_quantity_positive_gross_negative(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'Тип операции': 'Возврат',
                'Сумма операции (т)': '-6 600,00',
                'Сумма к зачислению/ списанию (т)': '-6 403,79',
                'Детали покупки': 'Chery салонный фильтр T218107011, 4 шт.',
            })])
            row = self._sync(path).rows[0]
            self.assertEqual(row.operation_type, OP_RETURN)
            self.assertEqual(row.quantity, 4)
            self.assertEqual(row.gross_amount, Decimal('-6600.00'))
            self.assertLess(row.gross_amount, 0)

    def test_unit_gross_amount_is_abs_gross_divided_by_quantity(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'Тип операции': 'Возврат',
                'Сумма операции (т)': '-6 600,00',
                'Детали покупки': 'Воздушный фильтр S3010140903, 5 шт.',
            })])
            row = self._sync(path).rows[0]
            self.assertEqual(row.quantity, 5)
            self.assertEqual(row.unit_gross_amount, Decimal('1320.00'))

    def test_exact_product_article_match(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            row = self._sync(path).rows[0]
            self.assertEqual(row.product_id, self.product.pk)
            self.assertEqual(row.matched_identifier, 'T151109111')

    def test_merchant_sku_match(self):
        cabin = _make_product(self.seller, 'AF-CABIN')
        listing = ProductKaspiListing.objects.create(
            product=cabin,
            master_sku='200200',
            merchant_sku='T218107011',
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'Детали покупки': 'Chery салонный фильтр T218107011, 4 шт.',
                'Сумма операции (т)': '6 600,00',
            })])
            row = self._sync(path).rows[0]
            self.assertEqual(row.product_id, cabin.pk)
            self.assertEqual(row.listing_id, listing.pk)
            self.assertEqual(row.match_status, MATCH_LISTING)

    def test_product_not_found_operation_valid_unmatched(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'Детали покупки': 'Воздушный фильтр S3010140903, 5 шт.',
                'Сумма операции (т)': '5 000,00',
            })])
            before = _snapshot(self.seller)
            result = self._sync(path, apply=True)
            row = result.rows[0]
            self.assertEqual(row.status, STATUS_CREATED)
            self.assertEqual(row.match_status, MATCH_UNMATCHED)
            op = KaspiSalesOperation.objects.get()
            self.assertIsNone(op.product_id)
            self.assertIsNone(op.listing_id)
            self._assert_stock_untouched(before)

    def test_ambiguous_product_operation_valid(self):
        other_product = _make_product(self.seller, 'T218107011')
        ProductKaspiListing.objects.create(
            product=other_product,
            master_sku='300300',
            merchant_sku='T218107011',
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'Детали покупки': 'Набор T151109111 и T218107011',
                'Сумма операции (т)': '8 000,00',
            })])
            result = self._sync(path, apply=True)
            row = result.rows[0]
            self.assertEqual(row.status, STATUS_CREATED)
            self.assertEqual(row.match_status, MATCH_AMBIGUOUS)
            op = KaspiSalesOperation.objects.get()
            self.assertIsNone(op.product_id)
            self.assertIsNone(op.listing_id)

    def test_one_product_one_listing_listing_matched(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].match_status, MATCH_LISTING)
            op = KaspiSalesOperation.objects.get()
            self.assertEqual(op.product_id, self.product.pk)
            self.assertEqual(op.listing_id, self.listing.pk)

    def test_one_product_several_listings_product_only(self):
        self.listing.merchant_sku = 'KASPI-A'
        self.listing.save(update_fields=['merchant_sku'])
        ProductKaspiListing.objects.create(
            product=self.product,
            master_sku='100501',
            merchant_sku='KASPI-B',
        )
        details = 'Chery воздушный фильтр T151109111'
        self.assertEqual(ProductKaspiListing.objects.filter(product=self.product).count(), 2)
        self.assertIn(self.product.article, details)
        self.assertNotIn('KASPI-A', details)
        self.assertNotIn('KASPI-B', details)
        self.assertNotIn('100500', details)
        self.assertNotIn('100501', details)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{'Детали покупки': details})])
            result = self._sync(path, apply=True)
            row = result.rows[0]
            self.assertEqual(row.match_status, MATCH_PRODUCT)
            self.assertIsNone(row.listing_id)
            op = KaspiSalesOperation.objects.get()
            self.assertEqual(op.product_id, self.product.pk)
            self.assertIsNone(op.listing_id)
            self.assertEqual(op.match_status, MATCH_PRODUCT)

    def test_several_rows_same_order_id_one_kaspi_order(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [
                _sales_row(**{
                    '№ документа Покупки/Возврата': 'DOC-A',
                    'Время': '10:00:00',
                    'Детали покупки': 'Chery воздушный фильтр T151109111',
                }),
                _sales_row(**{
                    '№ документа Покупки/Возврата': 'DOC-B',
                    'Время': '11:00:00',
                    'Сумма операции (т)': '2 000,00',
                    'Детали покупки': 'Chery воздушный фильтр T151109111',
                }),
            ])
            self._sync(path, apply=True)
            self.assertEqual(KaspiOrder.objects.count(), 1)
            self.assertEqual(KaspiSalesOperation.objects.count(), 2)
            order = KaspiOrder.objects.get()
            self.assertEqual(order.external_order_id, 'RRN-100')
            self.assertEqual(order.operations.count(), 2)

    def test_purchase_and_return_same_order_coexist(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [
                _sales_row(**{
                    '№ документа Покупки/Возврата': 'DOC-P',
                    'Время': '10:00:00',
                }),
                _sales_row(**{
                    'Тип операции': 'Возврат',
                    '№ документа Покупки/Возврата': 'DOC-R',
                    'Время': '18:00:00',
                    'Сумма операции (т)': '-1 700,00',
                    'Сумма к зачислению/ списанию (т)': '-1 503,79',
                }),
            ])
            result = self._sync(path, apply=True)
            self.assertEqual(result.summary['purchases'], 1)
            self.assertEqual(result.summary['returns'], 1)
            self.assertEqual(
                result.summary['purchase_qty'] - result.summary['return_qty'],
                0,
            )
            self.assertEqual(KaspiOrder.objects.count(), 1)
            types = set(
                KaspiSalesOperation.objects.values_list('operation_type', flat=True)
            )
            self.assertEqual(types, {OP_PURCHASE, OP_RETURN})

    def test_dry_run_zero_db_writes(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            before = _snapshot(self.seller)
            batches_before = CatalogImportBatch.objects.count()
            result = self._sync(path)
            self.assertEqual(result.rows[0].status, STATUS_WOULD_CREATE)
            self.assertIsNone(result.batch_id)
            self.assertEqual(KaspiOrder.objects.count(), 0)
            self.assertEqual(KaspiSalesOperation.objects.count(), 0)
            self.assertEqual(CatalogImportBatch.objects.count(), batches_before)
            self._assert_stock_untouched(before)

    def test_apply_creates_order_and_operation(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            before = _snapshot(self.seller)
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_CREATED)
            self.assertEqual(KaspiOrder.objects.count(), 1)
            self.assertEqual(KaspiSalesOperation.objects.count(), 1)
            op = KaspiSalesOperation.objects.get()
            self.assertEqual(op.gross_amount, Decimal('1700.00'))
            self.assertEqual(op.commission_amount, Decimal('196.21'))
            self.assertEqual(
                op.import_batch.source,
                CatalogImportBatch.SOURCE_KASPI_SALES_REPORT,
            )
            self._assert_stock_untouched(before)

    def test_reapply_same_file_zero_new_operations(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            self._sync(path, apply=True)
            order = KaspiOrder.objects.get()
            first_at = order.first_operation_at
            last_at = order.last_operation_at
            before = _snapshot(self.seller)
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_ALREADY_IMPORTED)
            self.assertEqual(result.summary['would_create_operations'], 0)
            self.assertEqual(KaspiSalesOperation.objects.count(), 1)
            order.refresh_from_db()
            self.assertEqual(order.first_operation_at, first_at)
            self.assertEqual(order.last_operation_at, last_at)
            self._assert_stock_untouched(before)

    def test_three_applies_same_business_rows_are_idempotent(self):
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / 'sales-a.csv'
            second = Path(tmp) / 'sales-a.csv'
            third = Path(tmp) / 'sales-overlap.csv'
            _write_csv(first, [_sales_row()])
            first_result = self._sync(first, apply=True)
            self.assertEqual(first_result.rows[0].status, STATUS_CREATED)
            self.assertEqual(KaspiOrder.objects.count(), 1)
            self.assertEqual(KaspiSalesOperation.objects.count(), 1)
            fingerprint = KaspiSalesOperation.objects.get().source_fingerprint
            second_result = self._sync(second, apply=True)
            self.assertEqual(second_result.rows[0].status, STATUS_ALREADY_IMPORTED)
            self.assertEqual(KaspiSalesOperation.objects.count(), 1)
            _write_csv(third, [_sales_row()], metadata=[
                ['Период', '01.07.2026 - 30.09.2026'],
                ['ИИН/БИН', '123456789012'],
                ['Наименование', 'AG Parts'],
                [],
            ])
            third_result = self._sync(third, apply=True)
            self.assertEqual(third_result.rows[0].status, STATUS_ALREADY_IMPORTED)
            self.assertEqual(third_result.rows[0].fingerprint, fingerprint)
            self.assertNotEqual(third_result.source_sha256, first_result.source_sha256)
            self.assertEqual(KaspiOrder.objects.count(), 1)
            self.assertEqual(KaspiSalesOperation.objects.count(), 1)
            self.assertEqual(CatalogImportBatch.objects.count(), 3)

    def test_fingerprint_ignores_filename_sha256_and_row(self):
        at = timezone.make_aware(datetime(2026, 8, 31, 16, 32, 54))
        base = {
            'seller_id': self.seller.pk,
            'external_order_id': 'RRN-100',
            'operation_at': at,
            'operation_type': OP_PURCHASE,
            'purchase_return_document': 'DOC-1',
            'details': 'Chery воздушный фильтр T151109111',
            'gross_amount': Decimal('1700.00'),
            'settlement_amount': Decimal('1503.79'),
        }
        fp1 = operation_fingerprint(**base)
        fp2 = operation_fingerprint(**{
            **base,
            'gross_amount': Decimal('1700'),
            'details': 'Chery\u00a0воздушный фильтр  T151109111',
        })
        self.assertEqual(fp1, fp2)
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / 'file-one.csv'
            second = Path(tmp) / 'file-two.csv'
            _write_csv(first, [_sales_row()])
            _write_csv(second, [_sales_row()], metadata=[
                ['Период', 'other period'],
                ['ИИН/БИН', '000'],
                ['Наименование', 'AG Parts'],
                [],
            ])
            row_one = self._sync(first).rows[0]
            row_two = self._sync(second).rows[0]
            self.assertEqual(row_one.fingerprint, row_two.fingerprint)
            self.assertNotEqual(Path(first).name, Path(second).name)

    def test_overlap_other_file_fingerprint_prevents_duplicate(self):
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / 'sales-aug.csv'
            second = Path(tmp) / 'sales-aug-sep.csv'
            _write_csv(first, [_sales_row()])
            _write_csv(second, [_sales_row()], metadata=[
                ['Период', '01.07.2026 - 30.09.2026'],
                ['ИИН/БИН', '123456789012'],
                ['Наименование', 'AG Parts'],
                [],
            ])
            self._sync(first, apply=True)
            result = self._sync(second, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_ALREADY_IMPORTED)
            self.assertEqual(KaspiSalesOperation.objects.count(), 1)
            fingerprints = list(
                KaspiSalesOperation.objects.values_list('source_fingerprint', flat=True)
            )
            self.assertEqual(len(fingerprints), 1)
            self.assertNotEqual(result.source_sha256, KaspiSalesOperation.objects.get().source_sha256)

    def test_invalid_amount_skipped(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{'Сумма операции (т)': 'not-a-number'})])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_INVALID)
            self.assertEqual(result.rows[0].reason, 'invalid_gross_amount')
            self.assertEqual(KaspiSalesOperation.objects.count(), 0)

    def test_invalid_date_skipped(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{'Дата операции': '32.13.2026'})])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_INVALID)
            self.assertEqual(result.rows[0].reason, 'invalid_operation_datetime')
            self.assertEqual(KaspiSalesOperation.objects.count(), 0)

    def test_invalid_operation_type_skipped(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{'Тип операции': 'Корректировка'})])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_INVALID)
            self.assertEqual(result.rows[0].reason, 'invalid_operation_type')
            self.assertEqual(KaspiSalesOperation.objects.count(), 0)

    def test_purchase_negative_amount_invalid_sign(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{'Сумма операции (т)': '-1 700,00'})])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_INVALID)
            self.assertEqual(result.rows[0].reason, 'invalid_sign')
            self.assertEqual(result.rows[0].gross_amount, Decimal('-1700.00'))
            self.assertEqual(KaspiSalesOperation.objects.count(), 0)

    def test_return_positive_amount_invalid_sign(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'Тип операции': 'Возврат',
                'Сумма операции (т)': '1 700,00',
            })])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_INVALID)
            self.assertEqual(result.rows[0].reason, 'invalid_sign')
            self.assertEqual(KaspiSalesOperation.objects.count(), 0)

    def test_financial_decimal_parser_understands_spaced_comma(self):
        amount, error = parse_kaspi_decimal('1 700,00')
        self.assertEqual(amount, Decimal('1700.00'))
        self.assertEqual(error, '')
        amount, error = parse_kaspi_decimal('-6 600,00')
        self.assertEqual(amount, Decimal('-6600.00'))
        amount, error = parse_kaspi_decimal('-196,21')
        self.assertEqual(amount, Decimal('-196.21'))
        amount, error = parse_kaspi_decimal('173')
        self.assertEqual(amount, Decimal('173'))
        amount, error = parse_kaspi_decimal('')
        self.assertIsNone(amount)
        self.assertEqual(error, '')
        amount, error = parse_kaspi_decimal('yes')
        self.assertIsNone(amount)
        self.assertEqual(error, 'non_numeric')

    def test_source_raw_data_persisted(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            self._sync(path, apply=True)
            op = KaspiSalesOperation.objects.get()
            self.assertEqual(op.raw_data['order_id'], 'RRN-100')
            self.assertEqual(op.raw_data['details'], 'Chery воздушный фильтр T151109111')
            self.assertEqual(op.raw_data['gross'], '1 700,00')

    def test_catalog_import_batch_created_only_on_apply(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            self._sync(path)
            self.assertEqual(CatalogImportBatch.objects.count(), 0)
            result = self._sync(path, apply=True)
            self.assertEqual(CatalogImportBatch.objects.count(), 1)
            batch = CatalogImportBatch.objects.get()
            self.assertEqual(batch.pk, result.batch_id)
            self.assertEqual(batch.source, CatalogImportBatch.SOURCE_KASPI_SALES_REPORT)
            self.assertEqual(batch.mode, CatalogImportBatch.MODE_WRITE)
            self.assertEqual(batch.seller_profile_id, self.seller.pk)
            self.assertEqual(batch.filename, 'sales.csv')
            self.assertTrue(batch.file_sha256)
            self.assertGreaterEqual(batch.source_row_count, 1)
            self.assertEqual(batch.created_count, 1)

    def test_importer_does_not_change_warehouse_stock(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            before = _snapshot(self.seller)
            self._sync(path, apply=True)
            self._assert_stock_untouched(before)
            self.assertEqual(
                ProductWarehouseStock.objects.get(
                    product=self.product, warehouse=self.pp1
                ).quantity,
                3,
            )
            self.assertEqual(
                ProductWarehouseStock.objects.get(
                    product=self.product, warehouse=self.pp2
                ).quantity,
                25,
            )

    def test_importer_does_not_create_stock_movement(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            before = _snapshot(self.seller)
            self._sync(path, apply=True)
            self._assert_stock_untouched(before)
            self.assertEqual(
                StockMovement.objects.exclude(source='test').count(),
                0,
            )

    def test_importer_does_not_change_product_price_cost_stock_qty(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            self._sync(path, apply=True)
            self.product.refresh_from_db()
            self.assertEqual(self.product.price, 1700)
            self.assertEqual(self.product.cost_price, 400)
            self.assertEqual(self.product.stock_qty, 9)

    def test_importer_does_not_change_listing_last_known(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row()])
            self._sync(path, apply=True)
            self.listing.refresh_from_db()
            self.assertEqual(self.listing.last_known_our_price, 4500)
            self.assertEqual(self.listing.last_known_kaspi_qty, 25)
            self.assertEqual(self.listing.last_synced_at, self.listing_synced_at)

    def test_order_operation_window_expands_on_overlap_import(self):
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / 'later.csv'
            second = Path(tmp) / 'earlier.csv'
            _write_csv(first, [_sales_row(**{
                'Дата операции': '31.08.2026',
                'Время': '16:32:54',
                '№ документа Покупки/Возврата': 'DOC-LATE',
            })])
            _write_csv(second, [_sales_row(**{
                'Дата операции': '01.08.2026',
                'Время': '10:00:00',
                '№ документа Покупки/Возврата': 'DOC-EARLY',
                'Сумма операции (т)': '900,00',
            })])
            self._sync(first, apply=True)
            order = KaspiOrder.objects.get()
            self.assertEqual(_local_stamp(order.first_operation_at), '2026-08-31 16:32:54')
            self.assertEqual(_local_stamp(order.last_operation_at), '2026-08-31 16:32:54')
            self._sync(second, apply=True)
            order.refresh_from_db()
            self.assertEqual(KaspiOrder.objects.count(), 1)
            self.assertEqual(KaspiSalesOperation.objects.count(), 2)
            self.assertEqual(_local_stamp(order.first_operation_at), '2026-08-01 10:00:00')
            self.assertEqual(_local_stamp(order.last_operation_at), '2026-08-31 16:32:54')

    def test_other_seller_article_is_not_matched(self):
        _make_product(self.other, 'S3010140903')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'Детали покупки': 'Воздушный фильтр S3010140903, 5 шт.',
                'Сумма операции (т)': '5 000,00',
            })])
            row = self._sync(path).rows[0]
            self.assertEqual(row.match_status, MATCH_UNMATCHED)
            self.assertIsNone(row.product_id)

    def test_missing_order_id_skipped(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{'Номер заказа (ID/RRN)': ''})])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].reason, 'missing_order_id')
            self.assertEqual(KaspiSalesOperation.objects.count(), 0)

    def test_command_dry_run_and_apply(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sales.csv'
            _write_csv(path, [_sales_row(**{
                'ID терминала': 'CARD-PAN-SHOULD-NOT-PRINT',
                'Тип оплаты': 'Kaspi Gold',
            })])
            before = _snapshot(self.seller)
            out = StringIO()
            call_command(
                'sync_kaspi_sales_report',
                str(path),
                '--seller-profile-id',
                str(self.seller.pk),
                stdout=out,
            )
            text = out.getvalue()
            self.assertIn('mode: dry-run', text)
            self.assertIn('header_row: 5', text)
            self.assertEqual(KaspiSalesOperation.objects.count(), 0)
            self._assert_stock_untouched(before)
            apply_out = StringIO()
            call_command(
                'sync_kaspi_sales_report',
                str(path),
                '--seller-profile-id',
                str(self.seller.pk),
                '--apply',
                stdout=apply_out,
            )
            apply_text = apply_out.getvalue()
            self.assertIn('mode: apply', apply_text)
            self.assertEqual(KaspiSalesOperation.objects.count(), 1)
            self._assert_stock_untouched(before)
            self.assertNotIn('Kaspi Gold', apply_text)
            self.assertNotIn('Kaspi Gold', text)
            self.assertNotIn('123456789012', apply_text)
            self.assertNotIn('CARD-PAN-SHOULD-NOT-PRINT', apply_text)
            self.assertNotIn('CARD-PAN-SHOULD-NOT-PRINT', text)
