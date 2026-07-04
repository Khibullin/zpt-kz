from django.test import Client, TestCase, override_settings

from core.models import Request
from core.views import _description_with_request_link


@override_settings(PUBLIC_BASE_URL='https://zpt.kz', ALLOWED_HOSTS=['*'])
class ShortRequestLinkTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST='zpt.kz')
        self.req = Request.objects.create(
            transport_type='car',
            country='Китай',
            brand='Chery',
            model='Tiggo 7',
            category='Двигатель',
            city='Алматы',
            phone='77001112233',
            description='Нужен фильтр',
            status='sent',
        )

    def test_request_save_generates_short_token(self):
        self.assertTrue(self.req.short_token)
        self.assertEqual(len(self.req.short_token), 6)

    def test_short_request_redirect_to_secure_page(self):
        url = f'/r/{self.req.id}/{self.req.short_token}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            f'/my-request/{self.req.id}/{self.req.access_token}/',
            response['Location'],
        )

    def test_short_request_redirect_rejects_invalid_token(self):
        url = f'/r/{self.req.id}/badtok/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_description_with_request_link_uses_short_url(self):
        text = _description_with_request_link(self.req)
        self.assertIn(
            f'https://zpt.kz/r/{self.req.id}/{self.req.short_token}/',
            text,
        )
        self.assertNotIn(str(self.req.access_token), text)
