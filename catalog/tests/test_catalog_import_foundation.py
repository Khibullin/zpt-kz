from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook

from catalog.ag_parts_import import ImportResult
from catalog.ag_parts_import import upsert_product as real_upsert_product
from catalog.import_ops import import_archive_root, persist_import_batch, sha256_file
from catalog.models import (
    Brand,
    CarModel,
    CatalogImportBatch,
    CatalogImportItem,
    Category,
    Country,
    Product,
    ProductBarcode,
    ProductFulfillment,
    ProductImage,
    ProductKaspiListing,
    SellerProfile,
)

MINIMAL_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
    b'\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\x0d\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _xlsx(path, headers, rows, sheet='Sheet'):
    workbook = Workbook()
    sheet_obj = workbook.active
    sheet_obj.title = sheet
    sheet_obj.append(headers)
    for row in rows:
        sheet_obj.append(row)
    workbook.save(path)


class CatalogFoundationModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ag1', password='secret12345')
        self.other_user = User.objects.create_user('ag2', password='secret12345')
        self.seller = SellerProfile.objects.create(
            user=self.user, name='AG Parts', phone='77771111111', city='Алматы',
        )
        self.other = SellerProfile.objects.create(
            user=self.other_user, name='Other', phone='77772222222', city='Астана',
        )
        self.country = Country.objects.create(name='Китай')
        self.brand = Brand.objects.create(country=self.country, name='Chery')
        self.model = CarModel.objects.create(brand=self.brand, name='Tiggo 7')
        self.category = Category.objects.create(name='Фильтры')

    def _product(self, seller, article, **kwargs):
        defaults = {
            'title': f'{article}',
            'article': article,
            'seller_name': seller.name,
            'whatsapp_number': seller.phone,
            'seller_profile': seller,
            'city': seller.city,
            'status': 'hidden',
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def test_article_not_globally_unique(self):
        self._product(self.seller, 'CF-100')
        self._product(self.other, 'CF-100')
        self.assertEqual(Product.objects.filter(article='CF-100').count(), 2)

    def test_seller_profile_article_unique(self):
        self._product(self.seller, 'CF-100')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._product(self.seller, 'CF-100')

    def test_multiple_barcodes_per_product(self):
        product = self._product(self.seller, 'CF-100')
        ProductBarcode.objects.create(product=product, code='111', source='wms', is_primary=True)
        ProductBarcode.objects.create(product=product, code='222', source='wms')
        self.assertEqual(product.barcodes.count(), 2)

    def test_barcode_unique_only_inside_product(self):
        first = self._product(self.seller, 'CF-100')
        second = self._product(self.other, 'AF-200')
        ProductBarcode.objects.create(product=first, code='SAME', source='wms')
        ProductBarcode.objects.create(product=second, code='SAME', source='kaspi')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductBarcode.objects.create(product=first, code='SAME', source='manual')

    def test_multiple_kaspi_listings(self):
        product = self._product(self.seller, 'CF-100')
        ProductKaspiListing.objects.create(product=product, master_sku='111', merchant_sku='CF-100')
        ProductKaspiListing.objects.create(product=product, master_sku='222', merchant_sku='CF-100-B')
        self.assertEqual(product.kaspi_listings.count(), 2)

    def test_master_and_merchant_sku_are_separate(self):
        product = self._product(self.seller, 'CF-100')
        listing = ProductKaspiListing.objects.create(
            product=product,
            master_sku='601717696',
            merchant_sku='151000187AA',
        )
        listing.refresh_from_db()
        self.assertEqual(listing.master_sku, '601717696')
        self.assertEqual(listing.merchant_sku, '151000187AA')
        self.assertNotEqual(listing.master_sku, listing.merchant_sku)

    def test_publish_flags_default_false(self):
        product = self._product(self.seller, 'CF-100')
        self.assertFalse(product.publish_to_sellers)
        self.assertFalse(product.publish_to_kaspi)

    def test_effective_kaspi_publication_requires_three_flags(self):
        product = self._product(self.seller, 'CF-100', publish_to_kaspi=True)
        listing = ProductKaspiListing.objects.create(
            product=product,
            master_sku='1',
            publish_to_kaspi=True,
            is_active=True,
        )
        self.assertTrue(listing.is_effectively_published_to_kaspi())
        listing.is_active = False
        listing.save(update_fields=['is_active'])
        self.assertFalse(listing.is_effectively_published_to_kaspi())

    def test_fulfillment_external_id_can_stay_empty(self):
        product = self._product(self.seller, 'CF-100')
        fulfillment = ProductFulfillment.objects.create(product=product, source='wms')
        self.assertEqual(fulfillment.external_id, '')

    def test_default_import_archive_root_is_inside_media_root(self):
        media = Path(settings.MEDIA_ROOT)
        with override_settings(IMPORT_ARCHIVE_ROOT=''):
            root = import_archive_root()
        self.assertEqual(root, media / '_catalog_imports')
        self.assertEqual(root.parent, media)

    def test_catalog_package_has_no_research_article_lists(self):
        catalog_dir = Path(__file__).resolve().parent.parent
        forbidden = ('PROBABLE_ARTICLES', 'UNCERTAIN_ARTICLES', 'NON_CONFIRMED_ARTICLES')
        for path in catalog_dir.rglob('*.py'):
            if 'tests' in path.parts:
                continue
            text = path.read_text(encoding='utf-8')
            for token in forbidden:
                self.assertNotIn(
                    token,
                    text,
                    f'{path.name} must not contain {token}',
                )


class CatalogImportGuardTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.user = User.objects.create_user('agguard', password='secret12345')
        self.seller = SellerProfile.objects.create(
            user=self.user, name='AG Parts', phone='77773333333', city='Алматы',
        )
        self.country = Country.objects.create(name='Китай')
        self.haval = Brand.objects.create(country=self.country, name='Haval')
        self.chery = Brand.objects.create(country=self.country, name='Chery')
        self.jolion = CarModel.objects.create(brand=self.haval, name='Jolion')
        self.tiggo7 = CarModel.objects.create(brand=self.chery, name='Tiggo 7')
        Category.objects.create(name='Фильтры')
        self.price_xlsx = self.root / 'price.xlsx'
        self.photos = self.root / 'photos'
        self.photos.mkdir()
        self.archives = self.root / 'archives'
        self._settings = override_settings(IMPORT_ARCHIVE_ROOT=str(self.archives))
        self._settings.enable()
        self.addCleanup(self._settings.disable)

    def _write_articles(self, articles, price=1500):
        rows = [
            [article, 'AIR FILTER', f'Фильтр {article}', 'HAVAL', 'Jolion', f'HAVAL Jolion', price]
            for article in articles
        ]
        _xlsx(
            self.price_xlsx,
            ['Артикул', 'Категория', 'Название', 'Марка', 'Модель', 'Применимость', 'Цена'],
            rows,
        )

    def _run(self, *args, **kwargs):
        stdout = StringIO()
        call_command(
            'import_ag_parts',
            f'--price-xlsx={self.price_xlsx}',
            f'--photos={self.photos}',
            f'--seller-profile-id={self.seller.pk}',
            *args,
            stdout=stdout,
            **kwargs,
        )
        return stdout.getvalue()

    def _archive_paths(self, pattern='*'):
        if not self.archives.exists():
            return []
        return list(self.archives.rglob(pattern))

    def _existing_product(self, article, price):
        product = Product.objects.create(
            title=f'{article} original',
            article=article,
            seller_name=self.seller.name,
            whatsapp_number=self.seller.phone,
            seller_profile=self.seller,
            city=self.seller.city,
            status='active',
            price=price,
            publish_to_sellers=True,
            publish_to_kaspi=True,
        )
        product.selected_models.add(self.tiggo7)
        return product

    def _fake_full_success(self, articles, seller=None):
        seller = seller or self.seller
        batch = CatalogImportBatch.objects.create(
            seller_profile=seller,
            source=CatalogImportBatch.SOURCE_AG_PARTS,
            filename='previous.xlsx',
            file_sha256='a' * 64,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            mode=CatalogImportBatch.MODE_WRITE,
            source_scope=CatalogImportBatch.SCOPE_FULL,
            status=CatalogImportBatch.STATUS_SUCCESS,
            source_unique_count=len(articles),
            selected_count=len(articles),
        )
        CatalogImportItem.objects.bulk_create([
            CatalogImportItem(
                batch=batch,
                article=article,
                action=CatalogImportItem.ACTION_CREATED,
            )
            for article in articles
        ])
        return batch

    def test_importer_update_does_not_change_publish_flags(self):
        self._write_articles(['AF-200'])
        self._run('--source-scope=full')
        product = Product.objects.get(seller_profile=self.seller, article='AF-200')
        product.publish_to_sellers = True
        product.publish_to_kaspi = True
        product.status = 'active'
        product.save(update_fields=['publish_to_sellers', 'publish_to_kaspi', 'status'])
        self._write_articles(['AF-200'], price=1800)
        self._run('--source-scope=full')
        product.refresh_from_db()
        self.assertTrue(product.publish_to_sellers)
        self.assertTrue(product.publish_to_kaspi)
        self.assertEqual(product.status, 'active')
        self.assertEqual(product.price, 1800)

    def test_missing_source_does_not_change_status_or_publish(self):
        articles = [f'KEEP-{index}' for index in range(1, 11)]
        gone_article = 'GONE-1'
        self._write_articles(articles + [gone_article])
        self._run('--source-scope=full')
        gone = Product.objects.get(article=gone_article, seller_profile=self.seller)
        gone.status = 'active'
        gone.publish_to_sellers = True
        gone.publish_to_kaspi = True
        gone.save(update_fields=['status', 'publish_to_sellers', 'publish_to_kaspi'])
        self._write_articles(articles)
        self._run('--source-scope=full')
        gone.refresh_from_db()
        self.assertEqual(gone.status, 'active')
        self.assertTrue(gone.publish_to_sellers)
        self.assertTrue(gone.publish_to_kaspi)
        self.assertTrue(Product.objects.filter(pk=gone.pk).exists())

    def test_dry_run_does_not_create_batch_item_or_archive(self):
        self._write_articles(['AF-200'])
        report = self.root / 'dry'
        self._run('--dry-run', '--source-scope=full', f'--report={report}')
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(CatalogImportBatch.objects.count(), 0)
        self.assertEqual(CatalogImportItem.objects.count(), 0)
        self.assertFalse(self.archives.exists() and any(self.archives.rglob('*.xlsx')))
        self.assertTrue(report.with_suffix('.json').exists())

    def test_partial_57_to_33_is_not_shrink(self):
        articles = [f'SKU-{index}' for index in range(1, 58)]
        self._fake_full_success(articles)
        self._write_articles(articles)
        output = self._run('--articles=' + ','.join(articles[:33]))
        self.assertIn('source_scope: partial', output)
        self.assertEqual(Product.objects.filter(seller_profile=self.seller).count(), 33)
        batch = CatalogImportBatch.objects.latest('id')
        self.assertEqual(batch.source_scope, CatalogImportBatch.SCOPE_PARTIAL)
        self.assertEqual(batch.source_unique_count, 57)
        self.assertEqual(batch.selected_count, 33)
        self.assertEqual(batch.missing_from_source_count, 0)
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_SUCCESS)

    def test_full_500_to_73_is_blocked(self):
        previous = [f'SKU-{index}' for index in range(1, 501)]
        self._fake_full_success(previous)
        current = [f'SKU-{index}' for index in range(1, 74)]
        self._write_articles(current)
        with self.assertRaises(CommandError):
            self._run('--source-scope=full')
        self.assertEqual(Product.objects.count(), 0)
        batch = CatalogImportBatch.objects.exclude(
            status=CatalogImportBatch.STATUS_SUCCESS,
        ).latest('id')
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_BLOCKED)
        self.assertGreaterEqual(batch.missing_from_source_count, 10)
        self.assertTrue(batch.blocked_reason)
        self.assertEqual(batch.archive_status, CatalogImportBatch.ARCHIVE_NOT_APPLICABLE)
        self.assertFalse(batch.source_archive_path)
        self.assertFalse(self._archive_paths('source.xlsx'))
        self.assertFalse(self._archive_paths('report.json'))
        item_actions = set(
            CatalogImportItem.objects.filter(batch=batch).values_list('action', flat=True)
        )
        self.assertFalse(
            item_actions
            & {
                CatalogImportItem.ACTION_CREATED,
                CatalogImportItem.ACTION_UPDATED,
                CatalogImportItem.ACTION_UNCHANGED,
            }
        )

    def test_full_small_legitimate_change_passes(self):
        previous = [f'SKU-{index}' for index in range(1, 13)]
        self._fake_full_success(previous)
        self._write_articles(previous[:11])
        output = self._run('--source-scope=full')
        self.assertIn('MISSING=', output)
        self.assertEqual(Product.objects.filter(seller_profile=self.seller).count(), 11)
        batch = CatalogImportBatch.objects.latest('id')
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_SUCCESS)
        self.assertEqual(batch.missing_from_source_count, 1)

    def test_allow_source_shrink_without_reason_forbidden(self):
        previous = [f'SKU-{index}' for index in range(1, 40)]
        self._fake_full_success(previous)
        self._write_articles(previous[:5])
        with self.assertRaises(CommandError) as caught:
            self._run('--source-scope=full', '--allow-source-shrink')
        self.assertIn('source-shrink-reason', str(caught.exception))
        self.assertEqual(Product.objects.count(), 0)

    def test_allow_source_shrink_with_reason_saved(self):
        previous = [f'SKU-{index}' for index in range(1, 40)]
        self._fake_full_success(previous)
        self._write_articles(previous[:5])
        self._run(
            '--source-scope=full',
            '--allow-source-shrink',
            '--source-shrink-reason=intentional catalog cut',
        )
        self.assertEqual(Product.objects.filter(seller_profile=self.seller).count(), 5)
        batch = CatalogImportBatch.objects.latest('id')
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_SUCCESS)
        self.assertEqual(batch.allow_source_shrink_reason, 'intentional catalog cut')
        self.assertGreaterEqual(batch.missing_from_source_count, 10)

    def test_previous_batch_only_full_successful_write_same_seller_source(self):
        other_user = User.objects.create_user('other-guard', password='secret12345')
        other = SellerProfile.objects.create(
            user=other_user, name='Other', phone='77774444444', city='Астана',
        )
        self._fake_full_success([f'OTH-{i}' for i in range(40)], seller=other)
        CatalogImportBatch.objects.create(
            seller_profile=self.seller,
            source=CatalogImportBatch.SOURCE_AG_PARTS,
            filename='partial.xlsx',
            file_sha256='b' * 64,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            mode=CatalogImportBatch.MODE_WRITE,
            source_scope=CatalogImportBatch.SCOPE_PARTIAL,
            status=CatalogImportBatch.STATUS_SUCCESS,
            source_unique_count=40,
            selected_count=5,
        )
        self._write_articles([f'SKU-{i}' for i in range(5)])
        self._run('--source-scope=full')
        self.assertEqual(Product.objects.filter(seller_profile=self.seller).count(), 5)
        batch = CatalogImportBatch.objects.filter(seller_profile=self.seller).latest('id')
        self.assertIsNone(batch.previous_successful_batch_id)

    def test_changed_fields_recorded(self):
        self._write_articles(['AF-200'], price=1500)
        self._run('--source-scope=full')
        self._write_articles(['AF-200'], price=1800)
        self._run('--source-scope=full')
        item = CatalogImportItem.objects.filter(
            article='AF-200',
            action=CatalogImportItem.ACTION_UPDATED,
        ).latest('id')
        self.assertEqual(item.changed_fields['price']['old'], 1500)
        self.assertEqual(item.changed_fields['price']['new'], 1800)

    def test_source_sha256_and_archive_only_on_write(self):
        self._write_articles(['AF-200'])
        digest = sha256_file(self.price_xlsx)
        dry = self._run('--dry-run', '--source-scope=full')
        self.assertIn(digest, dry)
        self.assertEqual(CatalogImportBatch.objects.count(), 0)
        write = self._run('--source-scope=full')
        self.assertIn(digest, write)
        batch = CatalogImportBatch.objects.get()
        self.assertEqual(batch.file_sha256, digest)
        self.assertTrue(batch.source_archive_path)
        self.assertEqual(batch.archive_status, CatalogImportBatch.ARCHIVE_SUCCESS)
        self.assertFalse(batch.archive_error)
        archived = list(self.archives.rglob('source.xlsx'))
        self.assertEqual(len(archived), 1)
        reports = list(self.archives.rglob('report.json'))
        self.assertEqual(len(reports), 1)
        self.assertTrue(list(self.archives.rglob('report.csv')))

    def test_successful_write_archive_under_media_root(self):
        media = self.root / 'media_products'
        media.mkdir()
        self._write_articles(['AF-200'])
        with override_settings(MEDIA_ROOT=str(media), IMPORT_ARCHIVE_ROOT=''):
            self._run('--source-scope=full')
            batch = CatalogImportBatch.objects.get()
        self.assertTrue(batch.source_archive_path.startswith('_catalog_imports/'))
        self.assertEqual(batch.archive_status, CatalogImportBatch.ARCHIVE_SUCCESS)
        dest = media / '_catalog_imports'
        self.assertTrue(dest.exists())
        self.assertEqual(len(list(dest.rglob('source.xlsx'))), 1)
        self.assertEqual(len(list(dest.rglob('report.json'))), 1)
        self.assertEqual(len(list(dest.rglob('report.csv'))), 1)

    def test_destructive_guard_blocks_before_product_mutation(self):
        keep_article = 'AF-KEEP'
        previous = [keep_article] + [f'SKU-{index}' for index in range(1, 40)]
        self._fake_full_success(previous)
        product = Product.objects.create(
            title='KEEP TITLE',
            article=keep_article,
            seller_name=self.seller.name,
            whatsapp_number=self.seller.phone,
            seller_profile=self.seller,
            city=self.seller.city,
            status='active',
            price=9999,
            publish_to_sellers=True,
            publish_to_kaspi=True,
        )
        product.selected_models.add(self.tiggo7)
        product.main_image.save('keep-main.png', ContentFile(MINIMAL_PNG), save=True)
        gallery = ProductImage(product=product, sort_order=1)
        gallery.image.save('keep-gallery.png', ContentFile(MINIMAL_PNG), save=True)
        barcode = ProductBarcode.objects.create(
            product=product, code='KEEP-BARCODE', source='wms', is_primary=True,
        )
        listing = ProductKaspiListing.objects.create(
            product=product,
            master_sku='KEEP-MASTER',
            merchant_sku=keep_article,
            publish_to_kaspi=True,
            is_active=True,
        )
        snapshot = {
            'title': product.title,
            'price': product.price,
            'status': product.status,
            'publish_to_sellers': product.publish_to_sellers,
            'publish_to_kaspi': product.publish_to_kaspi,
            'model_ids': set(product.selected_models.values_list('id', flat=True)),
            'main_image': product.main_image.name,
            'gallery': list(product.images.values_list('id', 'image')),
            'barcode_ids': set(product.barcodes.values_list('id', 'code')),
            'listing_ids': set(
                product.kaspi_listings.values_list('id', 'master_sku', 'merchant_sku')
            ),
            'product_id': product.pk,
        }
        product_count = Product.objects.count()
        current = [keep_article] + [f'SKU-{index}' for index in range(1, 5)]
        self._write_articles(current, price=111)
        with self.assertRaises(CommandError) as caught:
            self._run('--source-scope=full')
        self.assertIn('Source shrink blocked', str(caught.exception))
        product.refresh_from_db()
        self.assertEqual(product.title, snapshot['title'])
        self.assertEqual(product.price, snapshot['price'])
        self.assertEqual(product.status, snapshot['status'])
        self.assertEqual(product.publish_to_sellers, snapshot['publish_to_sellers'])
        self.assertEqual(product.publish_to_kaspi, snapshot['publish_to_kaspi'])
        self.assertEqual(
            set(product.selected_models.values_list('id', flat=True)),
            snapshot['model_ids'],
        )
        self.assertEqual(product.main_image.name, snapshot['main_image'])
        self.assertEqual(
            list(product.images.values_list('id', 'image')),
            snapshot['gallery'],
        )
        self.assertEqual(
            set(product.barcodes.values_list('id', 'code')),
            snapshot['barcode_ids'],
        )
        self.assertEqual(
            set(product.kaspi_listings.values_list('id', 'master_sku', 'merchant_sku')),
            snapshot['listing_ids'],
        )
        self.assertEqual(Product.objects.count(), product_count)
        self.assertFalse(Product.objects.exclude(pk=snapshot['product_id']).exists())
        self.assertTrue(ProductBarcode.objects.filter(pk=barcode.pk, code='KEEP-BARCODE').exists())
        self.assertTrue(
            ProductKaspiListing.objects.filter(
                pk=listing.pk, master_sku='KEEP-MASTER', merchant_sku=keep_article,
            ).exists()
        )
        batch = CatalogImportBatch.objects.exclude(
            status=CatalogImportBatch.STATUS_SUCCESS,
        ).latest('id')
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_BLOCKED)
        self.assertEqual(batch.archive_status, CatalogImportBatch.ARCHIVE_NOT_APPLICABLE)
        self.assertFalse(
            CatalogImportItem.objects.filter(
                batch=batch,
                action__in=[
                    CatalogImportItem.ACTION_CREATED,
                    CatalogImportItem.ACTION_UPDATED,
                    CatalogImportItem.ACTION_UNCHANGED,
                ],
            ).exists()
        )
        self.assertFalse(self._archive_paths('source.xlsx'))

    def test_batch_success_only_after_successful_write(self):
        seen = {}
        real_persist = persist_import_batch

        def wrapping_persist(**kwargs):
            seen['product_count'] = Product.objects.filter(
                seller_profile=self.seller,
            ).count()
            seen['status'] = kwargs['status']
            return real_persist(**kwargs)

        self._write_articles(['AF-200'])
        with patch(
            'catalog.management.commands.import_ag_parts.persist_import_batch',
            side_effect=wrapping_persist,
        ):
            self._run('--source-scope=full')
        self.assertEqual(seen['status'], CatalogImportBatch.STATUS_SUCCESS)
        self.assertEqual(seen['product_count'], 1)
        batch = CatalogImportBatch.objects.get()
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_SUCCESS)
        self.assertEqual(Product.objects.filter(article='AF-200').count(), 1)
        self.assertTrue(self._archive_paths('source.xlsx'))

    def test_critical_write_exception_marks_batch_error(self):
        calls = {'n': 0}

        def flaky_upsert(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                return real_upsert_product(*args, **kwargs)
            raise DatabaseError('simulated write failure')

        self._write_articles(['AF-200', 'BF-300'])
        with patch(
            'catalog.management.commands.import_ag_parts.upsert_product',
            side_effect=flaky_upsert,
        ):
            with self.assertRaises(CommandError) as caught:
                self._run('--source-scope=full')
        self.assertIn('Import aborted', str(caught.exception))
        self.assertEqual(Product.objects.count(), 0)
        batch = CatalogImportBatch.objects.get()
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_ERROR)
        self.assertNotEqual(batch.status, CatalogImportBatch.STATUS_SUCCESS)
        self.assertEqual(batch.archive_status, CatalogImportBatch.ARCHIVE_NOT_APPLICABLE)
        self.assertFalse(batch.source_archive_path)
        self.assertFalse(
            CatalogImportItem.objects.filter(
                action__in=[
                    CatalogImportItem.ACTION_CREATED,
                    CatalogImportItem.ACTION_UPDATED,
                ],
            ).exists()
        )
        self.assertFalse(self._archive_paths('source.xlsx'))

    def test_write_row_error_rolls_back_entire_batch(self):
        product_a = self._existing_product('AF-A', 1000)
        product_b = self._existing_product('AF-B', 2000)
        product_c = self._existing_product('AF-C', 3000)
        snapshots = {
            product.article: {
                'price': product.price,
                'title': product.title,
                'status': product.status,
                'publish_to_sellers': product.publish_to_sellers,
                'publish_to_kaspi': product.publish_to_kaspi,
                'models': set(product.selected_models.values_list('id', flat=True)),
            }
            for product in (product_a, product_b, product_c)
        }
        self._write_articles(['AF-A', 'AF-B', 'AF-C'], price=111)

        def fake_upsert(row, *args, **kwargs):
            if row.article == 'AF-B':
                return ImportResult(
                    article='AF-B',
                    action='error',
                    source_row=row.source_row,
                    errors=['forced_row_error'],
                )
            return real_upsert_product(row, *args, **kwargs)

        with patch(
            'catalog.management.commands.import_ag_parts.upsert_product',
            side_effect=fake_upsert,
        ):
            with self.assertRaises(CommandError):
                self._run()
        self.assertEqual(Product.objects.count(), 3)
        for product in (product_a, product_b, product_c):
            product.refresh_from_db()
            snap = snapshots[product.article]
            self.assertEqual(product.price, snap['price'])
            self.assertEqual(product.title, snap['title'])
            self.assertEqual(product.status, snap['status'])
            self.assertEqual(product.publish_to_sellers, snap['publish_to_sellers'])
            self.assertEqual(product.publish_to_kaspi, snap['publish_to_kaspi'])
            self.assertEqual(
                set(product.selected_models.values_list('id', flat=True)),
                snap['models'],
            )
        batch = CatalogImportBatch.objects.get()
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_ERROR)
        self.assertEqual(batch.error_count, 1)
        self.assertEqual(batch.archive_status, CatalogImportBatch.ARCHIVE_NOT_APPLICABLE)
        self.assertFalse(self._archive_paths('source.xlsx'))
        self.assertFalse(
            CatalogImportItem.objects.filter(
                action__in=[
                    CatalogImportItem.ACTION_CREATED,
                    CatalogImportItem.ACTION_UPDATED,
                ],
            ).exists()
        )

    def test_write_unexpected_exception_rolls_back_entire_batch(self):
        product_a = self._existing_product('AF-A', 1000)
        product_b = self._existing_product('AF-B', 2000)
        product_c = self._existing_product('AF-C', 3000)
        self._write_articles(['AF-A', 'AF-B', 'AF-C'], price=111)

        def fake_upsert(row, *args, **kwargs):
            if row.article == 'AF-B':
                raise RuntimeError('unexpected boom')
            return real_upsert_product(row, *args, **kwargs)

        with patch(
            'catalog.management.commands.import_ag_parts.upsert_product',
            side_effect=fake_upsert,
        ):
            with self.assertRaises(CommandError) as caught:
                self._run()
        self.assertIn('unexpected boom', str(caught.exception))
        for product, price in ((product_a, 1000), (product_b, 2000), (product_c, 3000)):
            product.refresh_from_db()
            self.assertEqual(product.price, price)
            self.assertTrue(product.publish_to_sellers)
            self.assertEqual(
                set(product.selected_models.values_list('id', flat=True)),
                {self.tiggo7.pk},
            )
        self.assertEqual(Product.objects.count(), 3)
        batch = CatalogImportBatch.objects.get()
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_ERROR)
        self.assertEqual(batch.archive_status, CatalogImportBatch.ARCHIVE_NOT_APPLICABLE)
        self.assertFalse(self._archive_paths('source.xlsx'))

    def test_success_batch_rejected_when_row_has_error_action(self):
        with self.assertRaises(ValueError):
            persist_import_batch(
                seller=self.seller,
                source=CatalogImportBatch.SOURCE_AG_PARTS,
                filename='x.xlsx',
                file_sha256='c' * 64,
                started_at=timezone.now(),
                source_scope=CatalogImportBatch.SCOPE_PARTIAL,
                status=CatalogImportBatch.STATUS_SUCCESS,
                source_row_count=1,
                source_unique_count=1,
                selected_count=1,
                totals={'CREATED': 0, 'UPDATED': 0, 'ERROR': 1},
                missing_count=0,
                previous_batch=None,
                results=[
                    ImportResult(
                        article='AF-B',
                        action='error',
                        source_row=2,
                        errors=['forced'],
                    )
                ],
            )
        self.assertFalse(CatalogImportBatch.objects.exists())

    def test_archive_failure_after_db_commit_keeps_products(self):
        product = self._existing_product('AF-200', 1500)
        self._write_articles(['AF-200'], price=1800)
        with patch(
            'catalog.management.commands.import_ag_parts.archive_import_source',
            side_effect=OSError('disk full'),
        ):
            with self.assertRaises(CommandError) as caught:
                self._run()
        self.assertIn('source archive was not saved', str(caught.exception))
        product.refresh_from_db()
        self.assertEqual(product.price, 1800)
        batch = CatalogImportBatch.objects.get()
        self.assertEqual(batch.status, CatalogImportBatch.STATUS_SUCCESS)
        self.assertEqual(batch.error_count, 0)
        self.assertEqual(batch.archive_status, CatalogImportBatch.ARCHIVE_ERROR)
        self.assertIn('disk full', batch.archive_error)
        self.assertFalse(batch.source_archive_path)
        self.assertFalse(self._archive_paths('source.xlsx'))

    def test_dry_run_row_error_continues_and_writes_nothing(self):
        self._write_articles(['AF-A', 'AF-B', 'AF-C'])

        def fake_upsert(row, *args, **kwargs):
            if row.article == 'AF-B':
                raise RuntimeError('dry boom')
            return real_upsert_product(row, *args, **kwargs)

        with patch(
            'catalog.management.commands.import_ag_parts.upsert_product',
            side_effect=fake_upsert,
        ):
            output = self._run('--dry-run')
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(CatalogImportBatch.objects.count(), 0)
        self.assertIn('ERROR', output)
        self.assertFalse(self._archive_paths('source.xlsx'))

    def test_full_and_articles_rejected(self):
        self._write_articles(['AF-200', 'BF-300'])
        with self.assertRaises(CommandError):
            self._run('--source-scope=full', '--articles=AF-200')

    def test_barcode_sync_upsert_and_dry_run(self):
        self._write_articles(['AF-200'])
        self._run('--source-scope=full')
        wms = self.root / 'wms.xlsx'
        _xlsx(
            wms,
            ['Продукт', 'Штрих коды'],
            [['AF-200', '111\n222']],
        )
        call_command(
            'sync_product_barcodes',
            f'--xlsx={wms}',
            f'--seller-profile-id={self.seller.pk}',
            '--dry-run',
            stdout=StringIO(),
        )
        self.assertEqual(ProductBarcode.objects.count(), 0)
        call_command(
            'sync_product_barcodes',
            f'--xlsx={wms}',
            f'--seller-profile-id={self.seller.pk}',
            stdout=StringIO(),
        )
        product = Product.objects.get(article='AF-200')
        self.assertEqual(product.barcodes.count(), 2)
        _xlsx(wms, ['Продукт', 'Штрих коды'], [['AF-200', '111']])
        call_command(
            'sync_product_barcodes',
            f'--xlsx={wms}',
            f'--seller-profile-id={self.seller.pk}',
            stdout=StringIO(),
        )
        self.assertEqual(product.barcodes.count(), 2)
        self.assertFalse(ProductFulfillment.objects.exists())
