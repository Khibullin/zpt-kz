from django.test import TestCase, override_settings
from django.urls import resolve


@override_settings(PUBLIC_CATALOG_ENABLED=False)
class OfficeFirstMinimalPublicTests(TestCase):
    def test_home_is_db_free_and_links_to_office(self):
        with self.assertNumQueries(0):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/office/login/")
        self.assertNotContains(response, 'retail-results')

    def test_head_probe_does_not_query_products(self):
        with self.assertNumQueries(0):
            response = self.client.head("/")
        self.assertEqual(response.status_code, 200)

    def test_office_routes_are_not_replaced(self):
        self.assertEqual(resolve("/office/accounting/finance/").url_name, "finance")
        self.assertTrue(resolve("/office/procurement/").url_name)
        self.assertEqual(resolve("/office/login/").url_name, "office_login")

    def test_public_product_views_return_gone_without_database_work(self):
        for path in ("/market/", "/our-products/", "/avtozapchasti/toyota/"):
            with self.subTest(path=path):
                with self.assertNumQueries(0):
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 410)
                self.assertIn("noindex", response["X-Robots-Tag"])

    def test_sitemap_and_robots_do_not_query_catalogue(self):
        for path in ("/robots.txt", "/sitemap.xml"):
            with self.subTest(path=path):
                with self.assertNumQueries(0):
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
        self.assertNotIn("/sitemap-products.xml", self.client.get("/sitemap.xml").content.decode())
