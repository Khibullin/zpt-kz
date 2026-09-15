from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

from catalog.kaspi_listing_facts import (
    RECON_IN_SYNC,
    RECON_KASPI_HIGHER,
    RECON_NO_PP2_BALANCE,
    STATUS_AMBIGUOUS_LISTING,
    STATUS_DUPLICATE_MASTER_SKU,
    STATUS_INVALID_PRICE,
    STATUS_INVALID_QUANTITY,
    STATUS_MATCHED_NO_CHANGE,
    STATUS_MISSING_LISTING,
    STATUS_UPDATED,
    STATUS_WOULD_UPDATE,
    sync_kaspi_listing_facts,
)
from catalog.models import (
    CatalogImportBatch,
    KaspiListingFactSnapshot,
    Product,
    ProductKaspiListing,
    ProductWarehouseStock,
    SellerProfile,
    StockMovement,
    Warehouse,
)
from catalog.stock_service import set_stock_quantity
from catalog.warehouses import WAREHOUSE_CODE_PP2


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


class SyncKaspiListingFactsTests(TestCase):
    def setUp(self):
        self.seller = _make_seller('facts-own', 'AG Parts', '77771110001')
        self.other = _make_seller('facts-other', 'Other Shop', '77771110002')
        self.product = _make_product(self.seller, 'AF-200', price=1111, stock_qty=9)
        self.listing = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku='100500',
            merchant_sku='AF-200',
        )
        self.pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)
        set_stock_quantity(
            product=self.product,
            warehouse=self.pp2,
            new_quantity=25,
            source='test',
        )

    def _sync(self, path, apply=False, **kwargs):
        return sync_kaspi_listing_facts(
            path=path,
            seller=self.seller,
            apply=apply,
            sku_column='SKU',
            price_column='price',
            quantity_column='PP2',
            **kwargs,
        )

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['100500', 4500, 25]])
            stocks_before = list(
                ProductWarehouseStock.objects.order_by('pk').values_list(
                    'pk', 'quantity'
                )
            )
            moves_before = StockMovement.objects.count()
            snapshots_before = KaspiListingFactSnapshot.objects.count()
            result = self._sync(path)
            self.assertEqual(result.rows[0].status, STATUS_WOULD_UPDATE)
            self.assertEqual(result.rows[0].reconciliation, RECON_IN_SYNC)
            self.listing.refresh_from_db()
            self.assertIsNone(self.listing.last_known_our_price)
            self.assertIsNone(self.listing.last_known_kaspi_qty)
            self.assertIsNone(self.listing.last_synced_at)
            self.assertEqual(KaspiListingFactSnapshot.objects.count(), snapshots_before)
            self.assertEqual(
                list(
                    ProductWarehouseStock.objects.order_by('pk').values_list(
                        'pk', 'quantity'
                    )
                ),
                stocks_before,
            )
            self.assertEqual(StockMovement.objects.count(), moves_before)
            self.product.refresh_from_db()
            self.assertEqual(self.product.price, 1111)
            self.assertEqual(self.product.stock_qty, 9)

    def test_apply_updates_listing_and_creates_snapshot(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['100500', 4500, 25]])
            before_pp2 = ProductWarehouseStock.objects.get(
                product=self.product,
                warehouse=self.pp2,
            ).quantity
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_UPDATED)
            self.listing.refresh_from_db()
            self.assertEqual(self.listing.last_known_our_price, 4500)
            self.assertEqual(self.listing.last_known_kaspi_qty, 25)
            self.assertIsNotNone(self.listing.last_synced_at)
            self.assertEqual(KaspiListingFactSnapshot.objects.count(), 1)
            snap = KaspiListingFactSnapshot.objects.get()
            self.assertEqual(snap.listing_id, self.listing.pk)
            self.assertEqual(snap.observed_price, 4500)
            self.assertEqual(snap.observed_qty, 25)
            self.assertEqual(
                snap.source,
                KaspiListingFactSnapshot.SOURCE_ACTIVE_XLSX,
            )
            self.assertEqual(
                snap.import_batch.source,
                CatalogImportBatch.SOURCE_KASPI_LISTING_FACTS,
            )
            self.product.refresh_from_db()
            self.assertEqual(self.product.price, 1111)
            self.assertEqual(self.product.cost_price, 400)
            self.assertEqual(self.product.stock_qty, 9)
            after_pp2 = ProductWarehouseStock.objects.get(
                product=self.product,
                warehouse=self.pp2,
            ).quantity
            self.assertEqual(after_pp2, before_pp2)
            self.assertEqual(
                StockMovement.objects.exclude(source='test').count(),
                0,
            )

    def test_idempotent_reapply_same_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['100500', 4500, 25]])
            self._sync(path, apply=True)
            moves_before = StockMovement.objects.count()
            snapshots_before = KaspiListingFactSnapshot.objects.count()
            synced_at = ProductKaspiListing.objects.get(pk=self.listing.pk).last_synced_at
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_MATCHED_NO_CHANGE)
            self.assertEqual(
                KaspiListingFactSnapshot.objects.count(),
                snapshots_before,
            )
            self.assertEqual(StockMovement.objects.count(), moves_before)
            self.listing.refresh_from_db()
            self.assertEqual(self.listing.last_known_our_price, 4500)
            self.assertEqual(self.listing.last_known_kaspi_qty, 25)
            self.assertEqual(self.listing.last_synced_at, synced_at)

    def test_missing_listing_does_not_create_listing(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['999999', 100, 1]])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_MISSING_LISTING)
            self.assertEqual(ProductKaspiListing.objects.count(), 1)
            self.assertEqual(KaspiListingFactSnapshot.objects.count(), 0)

    def test_ambiguous_listing_writes_nothing(self):
        other_product = _make_product(self.seller, 'AF-201')
        ProductKaspiListing.objects.create(
            product=other_product,
            master_sku='100500',
            merchant_sku='AF-201',
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['100500', 4500, 25]])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_AMBIGUOUS_LISTING)
            self.listing.refresh_from_db()
            self.assertIsNone(self.listing.last_known_kaspi_qty)
            self.assertEqual(KaspiListingFactSnapshot.objects.count(), 0)

    def test_duplicate_master_sku_in_source_not_applied(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(
                path,
                ['SKU', 'price', 'PP2'],
                [['100500', 4500, 25], ['100500', 4600, 10]],
            )
            result = self._sync(path, apply=True)
            self.assertTrue(
                all(row.status == STATUS_DUPLICATE_MASTER_SKU for row in result.rows)
            )
            self.listing.refresh_from_db()
            self.assertIsNone(self.listing.last_known_our_price)
            self.assertEqual(KaspiListingFactSnapshot.objects.count(), 0)

    def test_invalid_price_and_qty_not_zero(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['100500', 'no', 25]])
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_INVALID_PRICE)
            self.listing.refresh_from_db()
            self.assertIsNone(self.listing.last_known_our_price)
            self.assertIsNone(self.listing.last_known_kaspi_qty)

            path2 = Path(tmp) / 'qty.xlsx'
            _xlsx(path2, ['SKU', 'price', 'PP2'], [['100500', 4500, 'preorder']])
            result2 = self._sync(path2, apply=True)
            self.assertEqual(result2.rows[0].status, STATUS_INVALID_QUANTITY)
            self.listing.refresh_from_db()
            self.assertIsNone(self.listing.last_known_kaspi_qty)
            self.assertEqual(KaspiListingFactSnapshot.objects.count(), 0)

    def test_pp2_protection_and_no_pp2_balance(self):
        other = _make_product(self.seller, 'NO-PP2')
        listing = ProductKaspiListing.objects.create(
            product=other,
            master_sku='200200',
            merchant_sku='NO-PP2',
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['200200', 800, 3]])
            before = list(
                ProductWarehouseStock.objects.order_by('pk').values_list(
                    'pk', 'product_id', 'quantity'
                )
            )
            result = self._sync(path, apply=True)
            self.assertEqual(result.rows[0].status, STATUS_UPDATED)
            self.assertEqual(result.rows[0].reconciliation, RECON_NO_PP2_BALANCE)
            listing.refresh_from_db()
            self.assertEqual(listing.last_known_kaspi_qty, 3)
            self.assertEqual(
                list(
                    ProductWarehouseStock.objects.order_by('pk').values_list(
                        'pk', 'product_id', 'quantity'
                    )
                ),
                before,
            )
            self.assertFalse(
                ProductWarehouseStock.objects.filter(
                    product=other,
                    warehouse=self.pp2,
                ).exists()
            )

    def test_multiple_listings_same_product_not_summed(self):
        listing_b = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku='100501',
            merchant_sku='AF-200-B',
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(
                path,
                ['SKU', 'price', 'PP2'],
                [['100500', 4500, 25], ['100501', 4500, 25]],
            )
            result = self._sync(path, apply=True)
            statuses = {row.master_sku: row for row in result.rows}
            self.assertEqual(statuses['100500'].reconciliation, RECON_IN_SYNC)
            self.assertEqual(statuses['100501'].reconciliation, RECON_IN_SYNC)
            self.assertEqual(statuses['100500'].pp2_qty, 25)
            self.assertEqual(statuses['100501'].pp2_qty, 25)
            self.assertNotEqual(
                statuses['100500'].observed_qty + statuses['100501'].observed_qty,
                statuses['100500'].pp2_qty,
            )
            self.assertEqual(result.products_with_multiple_listings, 1)
            self.listing.refresh_from_db()
            listing_b.refresh_from_db()
            self.assertEqual(self.listing.last_known_kaspi_qty, 25)
            self.assertEqual(listing_b.last_known_kaspi_qty, 25)
            self.assertEqual(
                ProductWarehouseStock.objects.get(
                    product=self.product,
                    warehouse=self.pp2,
                ).quantity,
                25,
            )

    def test_kaspi_higher_reconciliation(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['100500', 4500, 40]])
            result = self._sync(path)
            self.assertEqual(result.rows[0].reconciliation, RECON_KASPI_HIGHER)
            self.assertEqual(result.rows[0].qty_delta, 15)

    def test_product_price_not_changed(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['100500', 9999, 25]])
            self._sync(path, apply=True)
            self.product.refresh_from_db()
            self.assertEqual(self.product.price, 1111)

    def test_management_command_dry_run(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'active.xlsx'
            _xlsx(path, ['SKU', 'price', 'PP2'], [['100500', 4500, 25]])
            out = StringIO()
            call_command(
                'sync_kaspi_listing_facts',
                str(path),
                '--seller-profile-id',
                str(self.seller.pk),
                '--sku-column',
                'SKU',
                '--price-column',
                'price',
                '--quantity-column',
                'PP2',
                stdout=out,
            )
            text = out.getvalue()
            self.assertIn('mode: dry-run', text)
            self.assertIn('headers:', text)
            self.listing.refresh_from_db()
            self.assertIsNone(self.listing.last_known_kaspi_qty)
            self.assertEqual(KaspiListingFactSnapshot.objects.count(), 0)
            self.product.refresh_from_db()
            self.assertEqual(self.product.price, 1111)
            self.assertEqual(self.product.stock_qty, 9)
