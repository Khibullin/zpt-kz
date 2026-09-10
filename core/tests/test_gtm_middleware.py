import os
from unittest.mock import patch

from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase

from core.seo_middleware import SeoRobotsHeaderMiddleware


class GtmStandaloneMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _response(self, body, content_type='text/html; charset=utf-8'):
        return HttpResponse(body, content_type=content_type)

    def test_valid_gtm_is_injected_into_standalone_html(self):
        middleware = SeoRobotsHeaderMiddleware(
            lambda request: self._response('<html><head><title>X</title></head><body class="page">OK</body></html>')
        )
        request = self.factory.get('/parts-sellers/')

        with patch.dict(os.environ, {'GOOGLE_TAG_MANAGER_ID': 'GTM-ABC123'}, clear=False):
            response = middleware(request)

        html = response.content.decode()
        self.assertIn('googletagmanager.com/gtm.js?id=', html)
        self.assertIn('googletagmanager.com/ns.html?id=GTM-ABC123', html)
        self.assertLess(html.index('googletagmanager.com/gtm.js'), html.index('</head>'))
        self.assertGreater(html.index('googletagmanager.com/ns.html'), html.index('<body class="page">'))

    def test_existing_gtm_is_not_duplicated(self):
        body = (
            '<html><head><script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>'
            '</head><body>OK</body></html>'
        )
        middleware = SeoRobotsHeaderMiddleware(lambda request: self._response(body))
        request = self.factory.get('/')

        with patch.dict(os.environ, {'GOOGLE_TAG_MANAGER_ID': 'GTM-ABC123'}, clear=False):
            response = middleware(request)

        self.assertEqual(response.content.decode().count('googletagmanager.com'), 1)

    def test_gtm_is_not_injected_without_valid_id(self):
        middleware = SeoRobotsHeaderMiddleware(
            lambda request: self._response('<html><head></head><body>OK</body></html>')
        )
        request = self.factory.get('/parts-sellers/')

        with patch.dict(os.environ, {'GOOGLE_TAG_MANAGER_ID': 'G-INVALID'}, clear=False):
            response = middleware(request)

        self.assertNotIn('googletagmanager.com', response.content.decode())

    def test_non_html_response_is_not_modified(self):
        middleware = SeoRobotsHeaderMiddleware(lambda request: JsonResponse({'ok': True}))
        request = self.factory.get('/api/test/')

        with patch.dict(os.environ, {'GOOGLE_TAG_MANAGER_ID': 'GTM-ABC123'}, clear=False):
            response = middleware(request)

        self.assertEqual(response.json(), {'ok': True})
