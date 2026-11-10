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

    def test_source_block_does_not_stop_another_approved_manufacturer(self):
        SupplierPhotoTask.objects.filter(pk=self.task.pk).update(status='blocked',note='HTTP 403',evidence={'block_kind':'source'})
        product=Product.objects.create(title='555 SA-1631R',article='SA-1631R',seller_profile=self.product.seller_profile,price=1300,stock_qty=2)
        offer=Offer.objects.create(supplier=Supplier.objects.create(name='Фаетон'),brand='555',article='SA-1631R',name='Рычаг',cost=1000,stock='2')
        RetailListing.objects.create(product=product,offer=offer)
        task=SupplierPhotoTask.objects.create(product=product,offer=offer,status='maker_ready',source_url='https://www.sankei-555.com/search/detail.php?p_id=3&sch_p_555no=SA-1631R')
        html='<th>555 No.</th><td>SA-1631R</td><img id="rollover_view" src="https://www.sankei-555.com/save_img/products/3/l_01.jpg">'
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==')
        with tempfile.TemporaryDirectory() as folder,override_settings(MEDIA_ROOT=folder),patch('catalog.source_photos.fetch_bytes',side_effect=[html.encode(),png]) as fetch,patch('catalog.source_photos.time.sleep'):
            self.assertEqual(process_photo_tasks(),1)
            self.assertIn('sankei-555.com',fetch.call_args_list[0].args[0])
        task.refresh_from_db();self.assertEqual(task.status,'done')

    def test_storage_block_stops_all_sources(self):
        SupplierPhotoTask.objects.filter(pk=self.task.pk).update(status='blocked',evidence={'block_kind':'storage'})
        with patch('catalog.source_photos.fetch_bytes',side_effect=AssertionError('No disk expansion')):
            self.assertEqual(process_photo_tasks(),0)

    def test_full_queue_stages_sources_and_records_missing_sources_idempotently(self):
        from catalog.photo_queue import stage_all_photos
        self.task.delete()
        self.offer.supplier_url='https://omega-auto-parts.kz/part-1'
        Offer.objects.filter(pk=self.offer.pk).update(supplier_url=self.offer.supplier_url)
        other=Product.objects.create(title='OTHER-2',article='OTHER-2',seller_profile=self.product.seller_profile,price=1300,stock_qty=2)
        offer=Offer.objects.create(supplier=Supplier.objects.create(name='Фаетон'),brand='TEST',article='OTHER-2',name='Деталь',cost=1000,stock='2')
        RetailListing.objects.create(product=other,offer=offer)
        counts=stage_all_photos()
        self.assertEqual(counts['prepared'],1)
        self.assertEqual(counts['needs_source'],1)
        self.assertEqual(SupplierPhotoTask.objects.get(product=self.product).status,'prepared')
        self.assertEqual(SupplierPhotoTask.objects.get(product=other).status,'needs_source')
        stage_all_photos()
        self.assertEqual(SupplierPhotoTask.objects.count(),2)
        source=Offer.objects.create(supplier=self.offer.supplier,brand='TEST',article='OTHER-2',name='Деталь',cost=999,stock='0',supplier_url='https://omega-auto-parts.kz/other-2')
        stage_all_photos()
        queued=SupplierPhotoTask.objects.get(product=other)
        self.assertEqual(queued.status,'prepared')
        self.assertEqual(queued.offer_id,source.pk)
        self.assertEqual(RetailListing.objects.get(product=other).offer_id,offer.pk)

    def test_full_queue_uses_verified_manufacturer_page_when_supplier_has_no_link(self):
        from catalog.photo_queue import stage_all_photos
        self.task.delete()
        url='https://asva-trading.com/catalog/suspension/0120-002'
        Product.objects.filter(pk=self.product.pk).update(article='0120-002')
        Offer.objects.filter(pk=self.offer.pk).update(brand='ASVA',article='0120-002',characteristics={'manufacturer_applicability':{'status':'sourced','source_url':url}})
        stage_all_photos()
        task=SupplierPhotoTask.objects.get(product=self.product)
        self.assertEqual(task.status,'maker_ready')
        self.assertEqual(task.source_url,url)

    def test_full_queue_discovers_febest_page_without_supplier_link(self):
        from catalog.photo_queue import stage_all_photos
        self.task.delete()
        Product.objects.filter(pk=self.product.pk).update(article='0101-ACA30F')
        Offer.objects.filter(pk=self.offer.pk).update(brand='FEBEST',article='0101-ACA30F')
        counts=stage_all_photos()
        task=SupplierPhotoTask.objects.get(product=self.product)
        self.assertEqual(counts['web_ready'],1)
        self.assertEqual(task.status,'web_ready')
        self.assertEqual(task.source_url,'https://febest.com.ua/catalogue/?product=0101-ACA30F')
        self.assertTrue(task.evidence['source_probe'])
        stage_all_photos()
        self.assertEqual(SupplierPhotoTask.objects.count(),1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price,1300)
        self.assertEqual(self.product.stock_qty,2)

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

    def test_removing_one_product_photo_keeps_shared_file_available(self):
        from catalog.image_storage import clear_main_image
        from django.core.files.base import ContentFile
        with tempfile.TemporaryDirectory() as folder,override_settings(MEDIA_ROOT=folder):
            storage=self.product.main_image.storage
            name=save_shared_image(storage,b'shared-photo')
            self.product.main_image=name
            self.product.supplier_thumbnail=name
            clear_main_image(self.product)
            self.assertTrue(storage.exists(name))
            self.assertFalse(self.product.main_image)
            self.assertFalse(self.product.supplier_thumbnail)
            ordinary=storage.save('products/ordinary.jpg',ContentFile(b'ordinary'))
            self.product.main_image=ordinary
            clear_main_image(self.product)
            self.assertFalse(storage.exists(ordinary))

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
