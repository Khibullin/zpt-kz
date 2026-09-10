from django.test import TestCase


class PrivacyPageTests(TestCase):
    def test_privacy_page_is_public_and_contains_operator_details(self):
        response = self.client.get('/privacy/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Политика конфиденциальности ZPT.KZ')
        self.assertContains(response, 'ИП Квант')
        self.assertContains(response, '600710301320')
        self.assertContains(response, 'rkhaibullin@gmail.com')
        self.assertContains(response, '<meta name="robots" content="index, follow">', html=True)
        self.assertContains(response, '<link rel="canonical" href="https://zpt.kz/privacy/">', html=True)

    def test_main_and_portal_bases_expose_privacy_link(self):
        home = self.client.get('/')
        request_parts = self.client.get('/request-parts/')

        self.assertEqual(home.status_code, 200)
        self.assertEqual(request_parts.status_code, 200)
        self.assertContains(home, 'href="/privacy/"')
        self.assertContains(request_parts, 'href="/privacy/"')
