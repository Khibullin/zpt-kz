from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


@override_settings(PUBLIC_SITE_ENABLED=False, PUBLIC_CATALOG_ENABLED=False)
class PublicSuspensionTests(TestCase):
    def test_public_landing_and_office_login_remain_available(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Публичный сервис временно приостановлен")
        self.assertContains(home, "Служебный кабинет")
        self.assertEqual(self.client.get("/office/login/").status_code, 200)

    def test_public_routes_are_suspended(self):
        for path in (
            "/seller/ag-parts/", "/seller-login/", "/register/",
            "/parts-sellers/", "/request-parts/", "/market/",
            "/create-request/", "/api/countries/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response["Cache-Control"], "no-store")

    def test_authenticated_office_works(self):
        user = get_user_model().objects.create_superuser(
            username="suspension-admin", password="test-password"
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get("/office/").status_code, 200)

    def test_robots_disallows_all(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Disallow: /", response.content.decode())
