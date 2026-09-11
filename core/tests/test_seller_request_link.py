from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Request
from core.services.seller_request_access import (
    build_seller_request_access_url,
    create_seller_request_access,
)


def _make_request() -> Request:
    return Request.objects.create(
        transport_type='car',
        brand='SecretBrandXYZ',
        model='SecretModelXYZ',
        category='SecretCategoryXYZ',
        city='SecretCityXYZ',
        phone='77019990011',
        description='VIN SECRET123456789 buyer comment',
        status='sent',
    )


@override_settings(PUBLIC_BASE_URL='https://zpt.kz', ALLOWED_HOSTS=['*'])
class SellerRequestLinkTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST='zpt.kz')
        self.request_obj = _make_request()
        self.access = create_seller_request_access(request=self.request_obj)

    def test_valid_token_returns_200(self):
        url = reverse('seller_request_link', kwargs={'token': self.access.token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Заявка ZPT.KZ')
        self.assertContains(response, 'Ссылка действительна')

    def test_unknown_token_is_not_500(self):
        url = reverse('seller_request_link', kwargs={'token': 'unknown-token-value'})
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 500)
        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            'Ссылка недействительна или больше не доступна.',
            status_code=404,
        )
        self.assertNotContains(response, 'Страница не найдена', status_code=404)

    def test_expired_token_is_not_valid_page(self):
        self.access.expires_at = timezone.now() - timedelta(minutes=1)
        self.access.save(update_fields=['expires_at'])
        url = reverse('seller_request_link', kwargs={'token': self.access.token})
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(response.status_code, 410)
        self.assertContains(response, 'Срок действия ссылки истёк.', status_code=410)

    def test_page_has_noindex(self):
        url = reverse('seller_request_link', kwargs={'token': self.access.token})
        response = self.client.get(url)
        self.assertContains(response, 'noindex, nofollow')
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertNotContains(response, 'rel="canonical"')

    def test_create_does_not_accept_caller_token(self):
        with self.assertRaises(TypeError):
            create_seller_request_access(
                request=self.request_obj,
                token='weak-token',
            )

    def test_page_does_not_expose_request_fields(self):
        url = reverse('seller_request_link', kwargs={'token': self.access.token})
        response = self.client.get(url)
        body = response.content.decode('utf-8')
        self.assertNotIn(self.request_obj.phone, body)
        self.assertNotIn(self.request_obj.brand, body)
        self.assertNotIn(self.request_obj.model, body)
        self.assertNotIn(self.request_obj.category, body)
        self.assertNotIn(self.request_obj.city, body)
        self.assertNotIn(self.request_obj.description, body)
        self.assertNotIn('SECRET123456789', body)
        self.assertNotIn(str(self.request_obj.access_token), body)
        self.assertNotIn(self.access.token, body)

    def test_token_lookup_is_case_sensitive(self):
        mixed = self.access.token
        flipped = mixed.swapcase()
        if flipped == mixed:
            self.skipTest('token has no letters to flip case')
        url = reverse('seller_request_link', kwargs={'token': flipped})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_named_url_reverse(self):
        url = reverse('seller_request_link', kwargs={'token': self.access.token})
        self.assertEqual(url, f'/sr/{self.access.token}/')
        self.assertEqual(
            build_seller_request_access_url(self.access),
            f'https://zpt.kz/sr/{self.access.token}/',
        )

    def test_get_without_login(self):
        url = reverse('seller_request_link', kwargs={'token': self.access.token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_default_ttl_is_two_hours(self):
        delta = self.access.expires_at - self.access.created_at
        self.assertGreaterEqual(delta.total_seconds(), 2 * 3600 - 5)
        self.assertLessEqual(delta.total_seconds(), 2 * 3600 + 5)
