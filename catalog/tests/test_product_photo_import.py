import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from catalog.models import (
    CatalogImportBatch,
    CatalogImportItem,
    Product,
    ProductImage,
    SellerProfile,
)
from catalog.product_photo_import import plan_product_photo_import


def _jpeg_bytes(color):
    buffer = BytesIO()
    Image.new('RGB', (12, 8), color).save(buffer, format='JPEG')
    return buffer.getvalue()


def _zip_bytes(mapping):
    buffer = BytesIO()
    items = mapping.items() if isinstance(mapping, dict) else mapping
    with zipfile.ZipFile(buffer, 'w') as bundle:
        for name, data in items:
            bundle.writestr(name, data)
    return buffer.getvalue()


def _make_seller(username, name, **kwargs):
    user = User.objects.create_user(username=username, password='secret12345')
    defaults = {
        'user': user,
        'name': name,
        'phone': '77770001122',
        'city': 'Алматы',
        'address': 'Тестовая, 1',
        'wholesale_enabled': True,
        'wholesale_min_order_qty': 10,
    }
    defaults.update(kwargs)
    return SellerProfile.objects.create(**defaults)


class ProductPhotoImportTests(TestCase):
    def setUp(self):
        self._media = TemporaryDirectory()
        self.addCleanup(self._media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self._media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.admin = User.objects.create_superuser(
            'photo-admin', 'admin@test.local', 'secret12345'
        )
        self.plain = User.objects.create_user('buyer', password='secret12345')
        self.seller = _make_seller('ag-parts-photo', 'AG Parts')
        self.assertEqual(self.seller.slug, 'ag-parts')
        self.other = _make_seller('photo-other', 'Other Shop', phone='77000000001')
        self.product = Product.objects.create(
            title='Воздушный фильтр Changan UNI-K',
            price=2750,
            seller_name=self.seller.name,
            seller_profile=self.seller,
            whatsapp_number='+77770001122',
            status='active',
            publish_to_sellers=True,
            city='Алматы',
            article='1109190CR01',
            slug='photo-cr01',
        )
        self.alias_product = Product.objects.create(
            title='Воздушный фильтр J691109111',
            price=1980,
            seller_name=self.seller.name,
            seller_profile=self.seller,
            whatsapp_number='+77770001122',
            status='active',
            publish_to_sellers=True,
            city='Алматы',
            article='J691109111',
            slug='photo-j69',
        )
        self.red = _jpeg_bytes((220, 20, 20))
        self.green = _jpeg_bytes((20, 180, 40))
        self.blue = _jpeg_bytes((30, 40, 200))
        self.photoroom = _jpeg_bytes((10, 10, 10))

    def _upload_url(self, seller=None):
        seller = seller or self.seller
        return reverse(
            'admin:catalog_sellerprofile_photo_import',
            args=[seller.pk],
        )

    def _apply_url(self, batch, seller=None):
        seller = seller or self.seller
        return reverse(
            'admin:catalog_sellerprofile_photo_apply',
            args=[seller.pk, batch.pk],
        )

    def _login_admin(self):
        self.client.force_login(self.admin)

    def _upload(self, payload, name='photos.zip'):
        self._login_admin()
        uploaded = SimpleUploadedFile(name, payload, content_type='application/zip')
        return self.client.post(self._upload_url(), {'file': uploaded})

    def _apply(self, batch, confirm='1'):
        self._login_admin()
        return self.client.post(
            self._apply_url(batch),
            {'confirm': confirm},
        )

    def test_preview_does_not_mutate_products_or_images(self):
        payload = _zip_bytes({
            '1109190CR01/front.jpg': self.red,
            '1109190CR01/back.jpg': self.green,
        })
        response = self._upload(payload)
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertFalse(self.product.main_image)
        self.assertEqual(self.product.images.count(), 0)
        self.assertEqual(ProductImage.objects.count(), 0)
        batch = CatalogImportBatch.objects.get()
        self.assertEqual(batch.source, CatalogImportBatch.SOURCE_PRODUCT_PHOTOS)
        self.assertEqual(batch.mode, CatalogImportBatch.MODE_DRY_RUN)
        self.assertTrue(batch.source_archive_path)
        item = batch.items.get(article='1109190CR01')
        self.assertEqual(item.action, CatalogImportItem.ACTION_UPDATED)
        self.assertEqual(item.changed_fields['status'], 'matched')
        self.assertEqual(item.changed_fields['photo_count'], 2)

    def test_whitelist_unknown_and_alias(self):
        payload = _zip_bytes({
            'J61109111/part-Photoroom.jpg': self.photoroom,
            'J61109111/side.jpg': self.red,
            'F081109111HD/skip.jpg': self.green,
            'UNKNOWN-SKU/a.jpg': self.blue,
            'FAE1109160/new.jpg': self.red,
        })
        rows = {row.folder_name: row for row in plan_product_photo_import(self.seller, payload)}
        self.assertEqual(rows['J61109111'].article, 'J691109111')
        self.assertEqual(rows['J61109111'].alias_used, 'J61109111')
        self.assertEqual(rows['J61109111'].product.pk, self.alias_product.pk)
        self.assertEqual(rows['J61109111'].display_status, 'matched')
        self.assertEqual(rows['F081109111HD'].display_status, 'skipped')
        self.assertIsNone(rows['F081109111HD'].product)
        self.assertEqual(rows['UNKNOWN-SKU'].display_status, 'skipped')
        self.assertEqual(rows['UNKNOWN-SKU'].action, CatalogImportItem.ACTION_SKIPPED)
        self.assertEqual(rows['FAE1109160'].display_status, 'missing')
        self.assertEqual(rows['FAE1109160'].action, CatalogImportItem.ACTION_SKIPPED)

    def test_traversal_rejected(self):
        payload = _zip_bytes({
            '../secret.jpg': self.red,
            '/tmp/abs.jpg': self.blue,
            '1109190CR01/ok.jpg': self.green,
        })
        rows = plan_product_photo_import(self.seller, payload)
        errors = [row for row in rows if row.action == CatalogImportItem.ACTION_ERROR]
        self.assertGreaterEqual(len(errors), 1)
        joined = ' '.join(' '.join(row.errors) for row in errors)
        self.assertIn('отклонён', joined)

    def test_unsupported_format_and_duplicate_filenames(self):
        payload = _zip_bytes([
            ('1109190CR01/photo.jpg', self.red),
            ('1109190CR01/photo.jpg', self.green),
            ('1109190CR01/notes.gif', b'GIF89a'),
            ('1109190CR01/readme.txt', b'not an image'),
        ])
        rows = {row.article: row for row in plan_product_photo_import(self.seller, payload)}
        matched = rows['1109190CR01']
        self.assertEqual(matched.display_status, 'matched')
        self.assertGreaterEqual(matched.photo_count, 1)
        self.assertTrue(all(Path(photo.name).suffix.lower() == '.jpg' for photo in matched.photos))

    def test_apply_requires_confirmation(self):
        payload = _zip_bytes({'1109190CR01/front.jpg': self.red})
        response = self._upload(payload)
        self.assertEqual(response.status_code, 302)
        batch = CatalogImportBatch.objects.get(mode=CatalogImportBatch.MODE_DRY_RUN)
        denied = self._apply(batch, confirm='')
        self.assertEqual(denied.status_code, 302)
        self.product.refresh_from_db()
        self.assertFalse(self.product.main_image)
        self.assertEqual(self.product.images.count(), 0)

    def test_apply_attaches_to_correct_product_and_is_idempotent(self):
        payload = _zip_bytes({
            '1109190CR01/front.jpg': self.red,
            '1109190CR01/side.jpg': self.green,
            'J61109111/filter-Photoroom.jpg': self.photoroom,
            'J61109111/back.jpg': self.blue,
        })
        response = self._upload(payload)
        self.assertEqual(response.status_code, 302)
        batch = CatalogImportBatch.objects.get(mode=CatalogImportBatch.MODE_DRY_RUN)
        applied = self._apply(batch)
        self.assertEqual(applied.status_code, 200)
        self.product.refresh_from_db()
        self.alias_product.refresh_from_db()
        self.assertTrue(self.product.main_image)
        self.assertEqual(self.product.images.count(), 1)
        self.assertIn('Photoroom', Path(self.alias_product.main_image.name).name)
        self.assertEqual(self.alias_product.images.count(), 1)
        first_main = self.product.main_image.name
        first_gallery = list(self.product.images.values_list('pk', 'image'))
        first_alias_count = self.alias_product.images.count()

        second_upload = self._upload(payload)
        self.assertEqual(second_upload.status_code, 302)
        second_batch = (
            CatalogImportBatch.objects.filter(mode=CatalogImportBatch.MODE_DRY_RUN)
            .order_by('-id')
            .first()
        )
        unchanged = second_batch.items.get(article='1109190CR01')
        self.assertEqual(unchanged.action, CatalogImportItem.ACTION_UNCHANGED)
        second_apply = self._apply(second_batch)
        self.assertEqual(second_apply.status_code, 200)
        self.product.refresh_from_db()
        self.alias_product.refresh_from_db()
        self.assertEqual(self.product.main_image.name, first_main)
        self.assertEqual(
            list(self.product.images.values_list('pk', 'image')),
            first_gallery,
        )
        self.assertEqual(self.alias_product.images.count(), first_alias_count)
        self.assertEqual(ProductImage.objects.filter(product=self.product).count(), 1)

    def test_anonymous_cannot_upload(self):
        payload = _zip_bytes({'1109190CR01/front.jpg': self.red})
        uploaded = SimpleUploadedFile('photos.zip', payload, content_type='application/zip')
        self.assertEqual(
            self.client.post(self._upload_url(), {'file': uploaded}).status_code,
            302,
        )
        self.client.force_login(self.plain)
        self.assertEqual(
            self.client.post(self._upload_url(), {'file': uploaded}).status_code,
            302,
        )
