import base64
import tempfile
import io
from PIL import Image
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.contrib.auth.models import User
from catalog.models import Product,SellerProfile,SupplierPhotoTask,PublicSitemapSnapshot,Category
from procurement.models import Supplier,Offer,RetailListing
from catalog.source_photos import matching_image,process_photo_tasks,SourceUnavailable,optimized_images,save_shared_image
from catalog.product_display import public_product_title
from core.seo_views import refresh_sitemap_snapshots
from django.core.cache import caches

class SupplierAssetTests(TestCase):
    def setUp(self):
        seller=SellerProfile.objects.create(user=User.objects.create(username='assets'),name='AG Parts',slug='ag-parts')
        self.product=Product.objects.create(title='PART-1_фильтр!',article='PART-1',seller_profile=seller,supplier='procurement',price=1300,stock_qty=2,description='Подробное описание товара из каталога поставщика, проверенный артикул.',category=Category.objects.create(name='Фильтры assets'))
        self.offer=Offer.objects.create(supplier=Supplier.objects.create(name='Omega'),brand='TEST',article='PART-1',name='Фильтр',cost=1000,stock='2')
        RetailListing.objects.create(product=self.product,offer=self.offer)
        self.task=SupplierPhotoTask.objects.create(product=self.product,offer=self.offer,source_url='https://omega-auto-parts.kz/part-1')
        self.photo='https://omega-auto-parts.kz/content/files/images/products_images/PART-1.jpg'
        self.html='<script type="application/ld+json">{"@graph":[{"@type":"Product","sku":"PART-1","brand":{"name":"TEST"},"image":"'+self.photo+'"}]}</script>'

    def test_photograph_requires_exact_identity_and_approved_url(self):
        self.assertEqual(matching_image(self.html,'TEST','PART-1'),self.photo)
        self.assertEqual(matching_image(self.html,'OTHER','PART-1'),'')
        self.assertEqual(matching_image(self.html,'TEST','PART-10'),'')
        self.assertEqual(matching_image(self.html.replace('PART-1.jpg','SPG.jpg'),'TEST','PART-1'),'')
        self.assertEqual(matching_image(self.html.replace('omega-auto-parts.kz/content','evil.example/content'),'TEST','PART-1'),'')

    def test_photo_is_added_once_without_touching_prices_or_stock(self):
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==')
        with tempfile.TemporaryDirectory() as folder,override_settings(MEDIA_ROOT=folder),patch('catalog.source_photos.fetch_bytes',side_effect=[self.html.encode(),png]),patch('catalog.source_photos.time.sleep'):
            self.assertEqual(process_photo_tasks(),1)
        self.task.refresh_from_db();self.product.refresh_from_db()
        self.assertEqual(self.task.status,'done')
        self.assertTrue(self.product.main_image)
        self.assertEqual(self.product.price,1300);self.assertEqual(self.product.stock_qty,2)
        with patch('catalog.source_photos.fetch_bytes',side_effect=AssertionError('No second download')):
            self.assertEqual(process_photo_tasks(),0)

    def test_formatted_sku_requires_exact_article_in_same_product_name(self):
        formatted=self.html.replace('"sku":"PART-1"','"sku":"PART1","name":"Фильтр PART-1"')
        self.assertEqual(matching_image(formatted,'TEST','PART-1'),self.photo)
        self.assertEqual(matching_image(formatted.replace('Фильтр PART-1','Фильтр PART-10'),'TEST','PART-1'),'')
        self.assertEqual(matching_image(formatted.replace('"sku":"PART1"','"sku":"PART10"'),'TEST','PART-1'),'')
        self.assertEqual(matching_image(formatted,'OTHER','PART-1'),'')

    def test_source_block_stops_pilot_without_retries(self):
        with patch('catalog.source_photos.fetch_bytes',side_effect=SourceUnavailable('HTTP 403')),patch('catalog.source_photos.time.sleep'):
            process_photo_tasks()
        self.task.refresh_from_db();self.assertEqual(self.task.status,'blocked')
        with patch('catalog.source_photos.fetch_bytes',side_effect=AssertionError('No bypass')):
            self.assertEqual(process_photo_tasks(),0)

    def test_compression_preserves_full_aspect_ratio_and_does_not_upscale(self):
        output=io.BytesIO()
        Image.new('RGB',(1600,800),'red').save(output,'PNG')
        main,thumb=optimized_images(output.getvalue())
        self.assertEqual(main[1],(800,400))
        self.assertEqual(thumb[1],(180,90))
        with Image.open(io.BytesIO(main[0])) as image:
            self.assertEqual(image.format,'WEBP')
        output=io.BytesIO()
        Image.new('RGB',(60,30),'blue').save(output,'PNG')
        self.assertEqual(optimized_images(output.getvalue())[0][1],(60,30))

    def test_identical_encoded_images_reuse_storage_file(self):
        from django.core.files.storage import FileSystemStorage
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            storage=FileSystemStorage(location=folder)
            first=save_shared_image(storage,b'identical')
            second=save_shared_image(storage,b'identical')
            self.assertEqual(first,second)
            self.assertEqual(len(list(Path(folder).rglob('*.webp'))),1)

    def test_manager_replacement_takes_priority_over_old_thumbnail(self):
        self.product.main_image='products/optimized/old.webp'
        self.product.supplier_thumbnail='products/optimized/small.webp'
        self.assertEqual(self.product.storefront_image.name,'products/optimized/small.webp')
        self.product.main_image='products/manager-replacement.jpg'
        self.assertEqual(self.product.storefront_image.name,'products/manager-replacement.jpg')

    def test_cleanup_preserves_sku_and_raw_supplier_title(self):
        self.assertEqual(public_product_title(self.product),'PART-1 — фильтр')
        self.assertEqual(self.product.title,'PART-1_фильтр!')
        self.assertEqual(self.product.article,'PART-1')

    @override_settings(SEO_PRODUCT_SITEMAP_ENABLED=True)
    def test_durable_xml_survives_memory_loss_without_catalogue_query(self):
        self.assertTrue(refresh_sitemap_snapshots())
        self.assertFalse(refresh_sitemap_snapshots())
        caches['seo_sitemaps'].clear()
        with patch('core.seo_views.sitemap_product_queryset',side_effect=AssertionError('No cold query')):
            response=self.client.get('/sitemap-products.xml')
            index=self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,self.product.slug)
        self.assertContains(index,'sitemap-products.xml')

    def test_observed_spider_filters_are_blocked_before_database_work(self):
        from django.test import RequestFactory
        from django.http import HttpResponse
        from core.crawler_budget import CrawlerFilterBudgetMiddleware
        called=[]
        middleware=CrawlerFilterBudgetMiddleware(lambda request:called.append(request.path) or HttpResponse('ok'))
        factory=RequestFactory()
        with self.assertNumQueries(0):
            blocked=middleware(factory.get('/?maker=TEST&picker=1',HTTP_USER_AGENT='Bytespider'))
        self.assertEqual(blocked.status_code,403)
        self.assertFalse(called)
        for path,agent in [('/?maker=TEST','Mozilla/5.0'),('/?maker=TEST','Googlebot'),('/?page=2','Bytespider'),('/part-1/','Bytespider'),('/sitemap.xml','Bytespider')]:
            self.assertEqual(middleware(factory.get(path,HTTP_USER_AGENT=agent)).status_code,200)

    def test_seo_heading_does_not_repeat_differently_formatted_article(self):
        from catalog.templatetags.product_extras import public_product_seo_name
        self.product.title='Фильтр SRR OE AF-69376 — упаковка 5 шт.'
        self.product.article='AF69376'
        self.assertEqual(public_product_seo_name(self.product),self.product.title)
        self.assertEqual(self.product.article,'AF69376')
        self.product.article='AF693760'
        self.assertTrue(public_product_seo_name(self.product).endswith('AF693760'))

    def test_formatted_article_match_requires_whole_token(self):
        from catalog.product_display import title_has_article
        self.assertTrue(title_has_article('Деталь E-01/STD','E01STD'))
        self.assertFalse(title_has_article('Деталь E01STDX','E01STD'))
        self.assertFalse(title_has_article('Деталь 123','А123'))
