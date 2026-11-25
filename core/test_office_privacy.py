import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core.domain_boundary import DomainBoundaryMiddleware
from core.office_privacy import protect_office_response
from core.seo_middleware import SeoRobotsHeaderMiddleware


class OfficePrivacyPolicyTests(SimpleTestCase):
    def setUp(self):
        self.factory=RequestFactory()

    def test_all_private_sections_skip_analytics(self):
        body='<html><head></head><body>private</body></html>'
        with patch.dict(os.environ, {'GOOGLE_TAG_MANAGER_ID':'GTM-ABC123'}):
            for path in ('/office/login/', '/office/accounting/', '/office/repricer/', '/admin/', '/control/', '/marketing/'):
                with self.subTest(path=path):
                    response=SeoRobotsHeaderMiddleware(lambda r:HttpResponse(body))(self.factory.get(path))
                    self.assertNotIn(b'googletagmanager',response.content)
                    self.assertIn('no-store',response['Cache-Control'])
                    request=self.factory.get(path)
                    template=Template('{% load analytics_tags %}{% google_tag_manager_head %}{% google_tag_manager_body %}')
                    self.assertEqual(template.render(Context({'request':request})), '')

    @override_settings(DOMAIN_BOUNDARY_ENABLED=True, ALLOWED_HOSTS=['zpt.kz'])
    def test_domain_guard_preserves_approved_private_image_cache(self):
        response=HttpResponse(b'image',content_type='image/jpeg')
        response['Cache-Control']='private, max-age=86400, immutable'
        response['Pragma']='no-cache'
        response.office_image_cache=True
        request=self.factory.get('/office/procurement/kaspi-products/?image=1',HTTP_HOST='zpt.kz')
        result=DomainBoundaryMiddleware(lambda r:protect_office_response(response))(request)
        self.assertEqual(result['Cache-Control'],'private, max-age=86400, immutable')
        self.assertNotIn('Pragma',result)
        self.assertIn('Cookie',result['Vary'])

    def test_image_flag_never_caches_html_or_error(self):
        for status,content_type in ((200,'text/html'),(403,'image/jpeg')):
            response=HttpResponse('private',status=status,content_type=content_type)
            response['Cache-Control']='private, max-age=86400'
            response.office_image_cache=True
            self.assertIn('no-store',protect_office_response(response)['Cache-Control'])

    @override_settings(DOMAIN_BOUNDARY_ENABLED=True, ALLOWED_HOSTS=['zpt.kz'])
    def test_versioned_static_assets_retain_their_cache(self):
        response=HttpResponse('css',content_type='text/css')
        response['Cache-Control']='public, max-age=31536000, immutable'
        request=self.factory.get('/static/office/theme.css',HTTP_HOST='zpt.kz')
        self.assertEqual(DomainBoundaryMiddleware(lambda r:response)(request)['Cache-Control'],'public, max-age=31536000, immutable')


@override_settings(DOMAIN_BOUNDARY_ENABLED=True, ALLOWED_HOSTS=['zpt.kz','agparts.kz'])
class PrivateMediaTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('media-audit',password='local-test')

    def test_private_document_requires_office_identity_and_is_never_publicly_cached(self):
        with TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=Path(tmp)):
            (Path(tmp)/'_catalog_imports').mkdir()
            (Path(tmp)/'_catalog_imports/private.csv').write_text('private')
            path='/products/_catalog_imports/private.csv'
            self.assertEqual(self.client.get(path,HTTP_HOST='zpt.kz').status_code,404)
            self.client.force_login(self.admin)
            self.assertEqual(self.client.get(path,HTTP_HOST='agparts.kz').status_code,404)
            response=self.client.get(path,HTTP_HOST='zpt.kz')
            self.assertEqual(response.status_code,200)
            self.assertIn('no-store',response['Cache-Control'])
            self.assertIn('attachment',response['Content-Disposition'])
            response.close()

    def test_symlink_in_product_folder_cannot_publish_customer_photo(self):
        with TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=Path(tmp)):
            root=Path(tmp)
            (root/'products').mkdir()
            (root/'request_photos').mkdir()
            private=root/'request_photos/customer.jpg'
            private.write_bytes(b'customer')
            (root/'products/alias.jpg').symlink_to(private)
            self.assertEqual(self.client.get('/products/products/alias.jpg',HTTP_HOST='agparts.kz').status_code,404)

    def test_public_product_photo_stays_available_with_public_cache(self):
        with TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=Path(tmp)):
            root=Path(tmp);(root/'products').mkdir()
            (root/'products/photo.webp').write_bytes(b'photo')
            response=self.client.get('/products/products/photo.webp',HTTP_HOST='agparts.kz')
            self.assertEqual(response.status_code,200)
            self.assertIn('public',response['Cache-Control'])
            response.close()

@override_settings(DOMAIN_BOUNDARY_ENABLED=True, PUBLIC_BASE_URL='https://agparts.kz')
class OfficeIntegrationUrlTests(SimpleTestCase):
    def test_notification_admin_links_use_closed_host(self):
        from core.services.operations_email_notifications import _admin_url as operations
        from core.services.seller_lead_pipeline_email import _admin_url as pipeline
        from core.services.platform_help_email import _admin_url as help_url
        for build in (operations,pipeline,help_url):
            self.assertTrue(build('admin:core_request_change',1).startswith('https://zpt.kz/admin/'))

    def test_kaspi_signed_images_use_office_integration_route(self):
        from types import SimpleNamespace
        from procurement.kaspi_catalog_api import signed_image_url
        url=signed_image_url(SimpleNamespace(pk=1,digest='test'))
        self.assertTrue(url.startswith('https://zpt.kz/office/procurement/'))

    @patch('procurement.kaspi_price_feed.feed_configured',return_value=True)
    @override_settings(KASPI_MERCHANT_ID='local-test')
    def test_price_feed_keeps_office_origin(self, configured):
        from procurement.kaspi_price_feed import feed_url
        self.assertTrue(feed_url().startswith('https://zpt.kz/office/procurement/'))
