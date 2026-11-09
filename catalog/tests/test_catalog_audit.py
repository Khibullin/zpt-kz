from unittest.mock import patch
from django.core.cache import caches
from django.test import TestCase, RequestFactory, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.contrib.auth.models import User
from catalog.models import Product, SellerProfile, Category
from catalog.storefront import retail_products, apply_filters, KEYS
from core.seo import canonical_url_for_request, robots_directive
from procurement.models import Supplier, Offer, RetailListing

class CatalogueAuditTests(TestCase):
    def setUp(self):
        caches['storefront_facets'].clear()
        caches['storefront_common'].clear()
        caches['seo_sitemaps'].clear()
        self.seller=SellerProfile.objects.create(user=User.objects.create(username='audit'),name='AG Parts',slug='ag-parts')
        self.category=Category.objects.create(name='Фильтры audit')
        self.p=Product.objects.create(title='Фильтр TEST',article='AUDIT-1',price=1300,stock_qty=2,seller_profile=self.seller,category=self.category)

    def test_cached_facets_reduce_queries_without_caching_price_or_stock(self):
        with CaptureQueriesContext(connection) as first:
            self.client.get('/')
        Product.objects.filter(pk=self.p.pk).update(price=2600,stock_qty=0)
        with CaptureQueriesContext(connection) as second:
            response=self.client.get('/')
        self.assertLess(len(second),len(first))
        self.assertEqual(response.context['products'][0].price,2600)
        self.assertFalse(response.context['products'][0].public_stock['can_buy'])

    def test_count_does_not_sort_wide_product_rows(self):
        selected={k:'' for k in KEYS}
        with CaptureQueriesContext(connection) as queries:
            apply_filters(retail_products(),selected).count()
        sql=queries[-1]['sql']
        self.assertNotIn('DISTINCT',sql)
        self.assertNotIn('description',sql)

    def test_exact_article_search_resolved_once(self):
        from catalog.article_utils import filter_products_by_public_query
        with patch('catalog.article_utils.filter_products_by_public_query',wraps=filter_products_by_public_query) as search:
            response=self.client.get('/',{'q':'AUDIT-1'})
        self.assertEqual(search.call_count,1)
        self.assertEqual(response.context['result_count'],1)

    def test_clean_pagination_is_crawlable_and_self_canonical(self):
        request=RequestFactory().get('/?page=2')
        self.assertEqual(robots_directive(request),'index, follow')
        self.assertEqual(canonical_url_for_request(request),'https://zpt.kz/?page=2')
        self.assertEqual(robots_directive(RequestFactory().get('/?page=2&maker=ABC')),'noindex, follow')
        Product.objects.bulk_create([Product(title=f'T{i}',seller_profile=self.seller) for i in range(25)])
        response=self.client.get('/')
        self.assertEqual(response.context['next_url'],'/?page=2')

    def test_robots_blocks_root_filter_combinations_but_not_other_routes(self):
        body=self.client.get('/robots.txt').content.decode()
        self.assertIn('Disallow: /?maker=',body)
        self.assertIn('Disallow: /?*&maker=',body)
        self.assertNotIn('Disallow: /*?*brand=',body)
        self.assertNotIn('Disallow: /?page=',body)

    @override_settings(SEO_PRODUCT_SITEMAP_ENABLED=True)
    def test_sourced_article_without_vehicle_or_photo_enters_paginated_sitemap(self):
        supplier=Supplier.objects.create(name='Omega')
        offer=Offer.objects.create(supplier=supplier,brand='BOSCH',article='AUDIT-1',name='Фильтр',cost=1000,stock='2')
        self.p.supplier='procurement';self.p.description='Описание детали, артикул и производитель подтверждены прайсом поставщика.';self.p.save()
        RetailListing.objects.create(offer=offer,product=self.p,enabled=True)
        with patch('core.seo_views.PRODUCT_SITEMAP_PAGE_SIZE',1):
            body=self.client.get('/sitemap-products.xml').content.decode()
            self.assertIn(self.p.slug,body)
            self.assertEqual(self.client.get('/sitemap-products-2.xml').status_code,404)
            # An unrelated card without photo/brand must not gain admission.
            self.p.pk=None;self.p.slug='supplier-second';self.p.article='AUDIT-2';self.p.save()
            offer.pk=None;offer.article='AUDIT-2';offer.save()
            RetailListing.objects.create(offer=offer,product=self.p,enabled=True)
            index=self.client.get('/sitemap.xml').content.decode()
            self.assertIn('/sitemap-products-2.xml',index)
            second=self.client.get('/sitemap-products-2.xml').content.decode()
            self.assertIn('supplier-second',second)
            self.assertNotIn('<loc>https://zpt.kz/filtr-test/',second)

    @override_settings(SEO_PRODUCT_SITEMAP_ENABLED=True)
    def test_sitemap_cache_avoids_catalogue_queries(self):
        self.client.get('/sitemap-products.xml')
        with self.assertNumQueries(0):
            response=self.client.get('/sitemap-products.xml')
        self.assertEqual(response.status_code,200)
        self.assertIn('max-age=300',response['Cache-Control'])

    def test_filter_traffic_cannot_evict_common_home_facets(self):
        self.client.get('/')
        self.assertTrue(caches['storefront_common']._cache)
        for n in range(40):
            caches['storefront_facets'].set(f'rare-{n}',n)
        with patch('catalog.storefront.root_vehicle_facet_counts') as counts:
            response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        counts.assert_not_called()

    def test_dynamic_catalogue_supports_compression(self):
        response=self.client.get('/',HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(response['Content-Encoding'],'gzip')
        import gzip
        self.assertIn(b'retail-results',gzip.decompress(response.content))

    def test_page_projection_loads_only_the_selected_cards(self):
        Product.objects.bulk_create([Product(title=f'Z part {i}',seller_profile=self.seller) for i in range(30)])
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get('/')
        self.assertEqual(len(response.context['products']),24)
        page_queries=[q['sql'] for q in queries if 'ORDER BY' in q['sql'] and 'LIMIT 24' in q['sql']]
        self.assertTrue(page_queries)
        self.assertNotIn('"description"',page_queries[0])

    @override_settings(SEO_PRODUCT_SITEMAP_ENABLED=True,SEO_EDITORIAL_ENABLED=True)
    def test_cached_index_respects_feature_switches(self):
        first=self.client.get('/sitemap.xml')
        self.assertContains(first,'sitemap-content.xml')
        with override_settings(SEO_EDITORIAL_ENABLED=False,SEO_PRODUCT_SITEMAP_ENABLED=False):
            changed=self.client.get('/sitemap.xml')
            self.assertNotContains(changed,'sitemap-content.xml')
            self.assertNotContains(changed,'sitemap-products.xml')
