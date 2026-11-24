from django.http import HttpResponse
from django.test import SimpleTestCase, RequestFactory, override_settings
from core.domain_boundary import DomainBoundaryMiddleware

@override_settings(ALLOWED_HOSTS=["zpt.kz", "agparts.kz", "www.agparts.kz", "testserver"])
class DomainBoundaryTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = DomainBoundaryMiddleware(lambda request: HttpResponse("unchanged"))

    def get(self, host, path):
        return self.middleware(self.factory.get(path, HTTP_HOST=host))

    @override_settings(DOMAIN_BOUNDARY_ENABLED=False)
    def test_disabled_preserves_existing_behavior(self):
        self.assertEqual(self.get("zpt.kz", "/").status_code, 200)

    @override_settings(DOMAIN_BOUNDARY_ENABLED=True)
    def test_store_cannot_reach_private_prefixes(self):
        for host in ("agparts.kz", "www.agparts.kz"):
            for path in ("/office", "/office/procurement/", "/admin/", "/control/", "/marketing/", "/internal/agparts/v1/products/"):
                with self.subTest(host=host, path=path):
                    response = self.get(host, path)
                    self.assertEqual(response.status_code, 404)
                    self.assertIn("noindex", response["X-Robots-Tag"])

    @override_settings(DOMAIN_BOUNDARY_ENABLED=True)
    def test_office_root_redirects(self):
        response = self.get("zpt.kz", "/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/office/login/")

    @override_settings(DOMAIN_BOUNDARY_ENABLED=True)
    def test_existing_office_and_public_routes_unchanged(self):
        self.assertEqual(self.get("zpt.kz", "/office/procurement/").status_code, 200)
        self.assertEqual(self.get("agparts.kz", "/market/").status_code, 200)
