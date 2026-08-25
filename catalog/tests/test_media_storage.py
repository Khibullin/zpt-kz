import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from backend.media_paths import resolve_media_root
from backend.media_views import serve_media
from catalog.models import Product, ProductImage


class ResolveMediaRootTests(TestCase):
    def setUp(self):
        self.base_dir = Path('/tmp/zpt-kz-project')

    def test_missing_env_keeps_local_products_directory(self):
        self.assertEqual(
            resolve_media_root(None, self.base_dir),
            self.base_dir / 'products',
        )
        self.assertEqual(
            resolve_media_root('', self.base_dir),
            self.base_dir / 'products',
        )
        self.assertEqual(
            resolve_media_root('   ', self.base_dir),
            self.base_dir / 'products',
        )

    def test_explicit_env_uses_given_path(self):
        self.assertEqual(
            resolve_media_root('/var/data', self.base_dir),
            Path('/var/data'),
        )
        self.assertEqual(
            resolve_media_root('  /var/data  ', self.base_dir),
            Path('/var/data'),
        )


class ProductImageUrlTests(TestCase):
    def test_existing_product_image_url_prefix_is_unchanged(self):
        product = Product.objects.create(
            title='Media URL probe',
            price=1000,
            seller_name='AG Parts',
            whatsapp_number='+77770000000',
            status='active',
            slug='media-url-probe',
            article='MEDIA-URL-1',
        )
        product.main_image.name = 'products/ag-parts-filter.webp'
        product.save(update_fields=['main_image'])
        gallery = ProductImage(product=product)
        gallery.image.name = 'products/ag-parts-filter-2.webp'
        gallery.save()

        self.assertEqual(
            product.main_image.url,
            '/products/products/ag-parts-filter.webp',
        )
        self.assertEqual(
            gallery.image.url,
            '/products/products/ag-parts-filter-2.webp',
        )


class ServeMediaRootTests(TestCase):
    def test_serve_media_reads_from_settings_media_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_root = Path(tmp)
            nested = media_root / 'products'
            nested.mkdir()
            probe = nested / 'probe.webp'
            probe.write_bytes(b'webp-from-custom-media-root')

            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.get('/products/products/probe.webp')

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'webp-from-custom-media-root')

    def test_serve_media_uses_settings_not_hardcoded_base_dir(self):
        source = serve_media.__code__.co_names
        self.assertIn('settings', source)
        self.assertIn('MEDIA_ROOT', source)
        self.assertNotIn('BASE_DIR', source)

    def test_default_storage_backend_is_filesystem(self):
        from django.conf import settings

        self.assertEqual(
            settings.STORAGES['default']['BACKEND'],
            'django.core.files.storage.FileSystemStorage',
        )
        self.assertEqual(settings.MEDIA_URL, '/products/')
