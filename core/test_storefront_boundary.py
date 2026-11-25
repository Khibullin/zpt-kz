from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import TestCase, RequestFactory, override_settings
from django.urls import resolve
from core.domain_boundary import DomainBoundaryMiddleware
from core.seo import canonical_url_for_path
from core.seo_views import sitemap_snapshot_key


@override_settings(DOMAIN_BOUNDARY_ENABLED=True, PUBLIC_SITE_ENABLED=False, PUBLIC_CATALOG_ENABLED=False, ALLOWED_HOSTS=['zpt.kz','agparts.kz','www.agparts.kz','zpt-kz-backend.onrender.com'])
class StoreBoundaryTests(TestCase):
    def test_private_routes_never_execute_even_with_staff_login(self):
        user = User.objects.create_user(username='domain-staff', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        for path in ('/office/', '/office/procurement/', '/office/accounting/', '/office/repricer/', '/admin/', '/control/', '/marketing/', '/internal/agparts/v1/products/', '/api/seller-profile/', '/api/seller-requests/', '/api/admin/seller/1/reset-password/', '/seller/dashboard/', '/seller/profile/', '/market/seller/dashboard/', '/market/ajax/commercial-price/'):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST='agparts.kz')
                self.assertEqual(response.status_code, 404)
                self.assertIn('no-store', response['Cache-Control'])

    def test_office_root_is_db_free_and_office_login_available(self):
        with self.assertNumQueries(0):
            response = self.client.get('/', HTTP_HOST='zpt.kz')
        self.assertEqual(response['Location'], '/office/login/')
        self.assertEqual(self.client.get('/office/login/', HTTP_HOST='zpt.kz').status_code, 200)
        self.assertEqual(self.client.get('/office/procurement/', HTTP_HOST='zpt.kz').status_code, 302)

    def test_zpt_public_redirect_and_store_alias(self):
        for host, path, target in [('zpt.kz','/avtozapchasti/toyota/','https://agparts.kz/avtozapchasti/toyota/'),('agparts.kz','/market/?q=123','https://agparts.kz/?q=123'),('www.agparts.kz','/?q=123','https://agparts.kz/?q=123')]:
            response = self.client.get(path, HTTP_HOST=host)
            self.assertEqual(response.status_code,301)
            self.assertEqual(response['Location'],target)

    def test_public_catalog_bypasses_legacy_guards_only_on_store_host(self):
        with patch('catalog.views.catalog_list', return_value=HttpResponse('storefront')):
            self.assertContains(self.client.get('/', HTTP_HOST='agparts.kz'), 'storefront')
        response=self.client.get('/robots.txt',HTTP_HOST='agparts.kz')
        self.assertContains(response, 'Sitemap: https://agparts.kz/sitemap.xml')
        self.assertNotContains(response, 'Sitemap: https://zpt.kz')
        self.assertContains(self.client.get('/robots.txt',HTTP_HOST='zpt.kz'),'Disallow: /')

    def test_safe_public_api_and_post_rejection(self):
        response = self.client.get('/api/countries/', HTTP_HOST='agparts.kz')
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.post('/api/countries/',{}, HTTP_HOST='agparts.kz').status_code,404)

    def test_authenticated_store_render_uses_anonymous_identity(self):
        request=RequestFactory().get('/our-products/', HTTP_HOST='agparts.kz')
        request.resolver_match=resolve(request.path)
        request.user=User(is_staff=True,is_superuser=True)
        middleware=DomainBoundaryMiddleware(lambda r: HttpResponse())
        self.assertIsNone(middleware.process_view(request,request.resolver_match.func,(),{}))
        self.assertIsInstance(request.user,AnonymousUser)

    def test_canonical_and_snapshot_origin_are_isolated(self):
        self.assertEqual(canonical_url_for_path('/market/abc/'),'https://agparts.kz/abc/')
        self.assertIn('https://agparts.kz',sitemap_snapshot_key('/sitemap.xml'))

    def test_media_documents_and_symlinks_are_not_public(self):
        with TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=Path(tmp)):
            root=Path(tmp)
            (root/'products').mkdir()
            (root/'products/photo.webp').write_bytes(b'photo')
            (root/'products/private.xlsx').write_bytes(b'private')
            (root/'procurement').mkdir()
            (root/'procurement/price.xlsx').write_bytes(b'private')
            (root/'request_photos').mkdir()
            (root/'request_photos/customer.jpg').write_bytes(b'private')
            for path in ('/products/products/private.xlsx','/products/procurement/price.xlsx','/products/request_photos/customer.jpg'):
                self.assertEqual(self.client.get(path,HTTP_HOST='agparts.kz').status_code,404)
            response=self.client.get('/products/products/photo.webp',HTTP_HOST='agparts.kz')
            self.assertEqual(response.status_code,200)
            response.close()

    def test_old_agparts_languages_redirect(self):
        self.assertEqual(self.client.get('/ru/',HTTP_HOST='agparts.kz')['Location'],'https://agparts.kz/')
        self.assertEqual(self.client.get('/kk/catalog/cabin/',HTTP_HOST='agparts.kz')['Location'],'https://agparts.kz/filters/cabin/')

from catalog.tests.test_storefront import StorefrontTests

@override_settings(DOMAIN_BOUNDARY_ENABLED=True, PUBLIC_SITE_ENABLED=False, PUBLIC_CATALOG_ENABLED=False, ALLOWED_HOSTS=['agparts.kz'])
class LiveHostStorefrontTests(StorefrontTests):
    def setUp(self):
        super().setUp()
        self.client.defaults['HTTP_HOST']='agparts.kz'

    def test_brand_and_product_metadata(self):
        from catalog.templatetags.product_extras import public_product_url
        response=self.client.get('/')
        self.assertContains(response,'/static/agparts/logo.png')
        self.assertNotContains(response,'ZPT.<em>KZ</em>')
        self.assertContains(response,'<link rel="canonical" href="https://agparts.kz/">')
        response=self.client.get(public_product_url(self.product))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'AG-101')
        self.assertContains(response,'https://agparts.kz/')
        self.assertNotContains(response,'href="/office/')

    def test_store_sitemap_origin(self):
        response=self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'https://agparts.kz/sitemap-static.xml')
        self.assertNotContains(response,'https://zpt.kz')
