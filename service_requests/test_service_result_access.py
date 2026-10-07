import json
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core.seo_views import STATIC_SITEMAP_PATHS
from service_requests.models import ServiceMatch, ServiceRequest, ServiceSeller

User = get_user_model()


def _request(**overrides):
    payload = {
        'service_type': 'sto',
        'city': 'Алматы',
        'district': 'Бостандыкский',
        'phone': '77009990001',
        'description': 'private result description',
    }
    payload.update(overrides)
    return ServiceRequest.objects.create(**payload)


def _secure_url(req):
    return reverse(
        'service_request_result_page',
        kwargs={
            'request_id': req.id,
            'access_token': req.access_token,
        },
    )


class ServiceRequestResultAccessTests(TestCase):
    def test_correct_token_opens_the_request(self):
        req = _request(phone='77009990011', description='only this request')
        response = self.client.get(_secure_url(req))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'only this request')
        self.assertContains(response, '77009990011')
        self.assertContains(response, 'Алматы')
        self.assertContains(response, f'Заявка №{req.id}')

    def test_wrong_token_hides_the_request(self):
        req = _request(phone='77009990022', description='hidden wrong token')
        url = reverse(
            'service_request_result_page',
            kwargs={
                'request_id': req.id,
                'access_token': uuid.uuid4(),
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertNotIn('77009990022', body)
        self.assertNotIn('hidden wrong token', body)
        self.assertNotIn('Бостандыкский', body)

    def test_unknown_request_id_is_not_found(self):
        response = self.client.get(
            reverse(
                'service_request_result_page',
                kwargs={
                    'request_id': 999999,
                    'access_token': uuid.uuid4(),
                },
            )
        )
        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertNotIn('77009990022', body)
        self.assertNotIn('hidden wrong token', body)

    def test_tokens_do_not_cross_requests(self):
        first = _request(phone='77009990031', description='request A secret')
        second = _request(phone='77009990032', description='request B secret')

        own_a = self.client.get(_secure_url(first))
        own_b = self.client.get(_secure_url(second))
        self.assertEqual(own_a.status_code, 200)
        self.assertEqual(own_b.status_code, 200)
        self.assertContains(own_a, 'request A secret')
        self.assertContains(own_b, 'request B secret')

        cross_a = self.client.get(
            reverse(
                'service_request_result_page',
                kwargs={
                    'request_id': first.id,
                    'access_token': second.access_token,
                },
            )
        )
        cross_b = self.client.get(
            reverse(
                'service_request_result_page',
                kwargs={
                    'request_id': second.id,
                    'access_token': first.access_token,
                },
            )
        )
        self.assertEqual(cross_a.status_code, 404)
        self.assertEqual(cross_b.status_code, 404)
        self.assertNotIn('request A secret', cross_a.content.decode())
        self.assertNotIn('request B secret', cross_b.content.decode())
        self.assertNotIn('77009990031', cross_b.content.decode())
        self.assertNotIn('77009990032', cross_a.content.decode())

    def test_legacy_page_url_does_not_reveal_the_request(self):
        req = _request(phone='77009990041', description='legacy page secret')
        response = self.client.get(f'/service-request/result/{req.id}/')
        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertIn('Ссылка на заявку недействительна или устарела.', body)
        self.assertNotIn('77009990041', body)
        self.assertNotIn('legacy page secret', body)
        self.assertNotIn('Бостандыкский', body)
        self.assertNotIn(f'Заявка №{req.id}', body)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertIn('nofollow', response['X-Robots-Tag'])

    def test_legacy_api_url_does_not_reveal_the_request(self):
        req = _request(phone='77009990042', description='legacy api secret')
        response = self.client.get(f'/api/service/result/{req.id}/')
        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertNotIn('77009990042', body)
        self.assertNotIn('legacy api secret', body)
        self.assertNotIn('Бостандыкский', body)

    def test_create_request_returns_secure_url_without_token_field(self):
        response = self.client.post(
            '/api/service/create-service-request/',
            data=json.dumps({
                'service_type': 'sto',
                'city': 'Кокшетау',
                'district': '',
                'phone': '77009990051',
                'description': 'created request secret',
                'services': ['Диагностика'],
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        req = ServiceRequest.objects.get(pk=data['request_id'])
        self.assertNotIn('access_token', data)
        self.assertEqual(data['result_url'], _secure_url(req))
        self.assertNotIn(f'/service-request/result/{req.id}/"', json.dumps(data))
        page = self.client.get(data['result_url'])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'created request secret')

    def test_secure_page_privacy_headers_and_meta(self):
        req = _request()
        response = self.client.get(_secure_url(req))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertIn('nofollow', response['X-Robots-Tag'])
        self.assertContains(response, 'name="robots"')
        self.assertContains(response, 'noindex,nofollow')
        self.assertNotContains(response, 'rel="canonical"')

    def test_ordinary_save_keeps_access_token(self):
        req = _request()
        original = req.access_token
        req.description = 'updated description'
        req.save()
        req.refresh_from_db()
        self.assertEqual(req.access_token, original)
        page = self.client.get(_secure_url(req))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'updated description')

    def test_seller_request_list_does_not_include_buyer_result_secret(self):
        user = User.objects.create_user(username='result-seller-user', password='USER-PASS')
        seller = ServiceSeller.objects.create(
            name='Result Seller',
            whatsapp='77771112233',
            password='',
            city='Алматы',
            seller_type='sto',
            user=user,
        )
        req = _request(phone='77009990061', description='seller must not see token')
        ServiceMatch.objects.create(request=req, seller=seller, status='new')
        client = Client()
        login = client.post(
            '/api/service/service-seller-login/',
            data=json.dumps({'whatsapp': seller.whatsapp, 'password': 'USER-PASS'}),
            content_type='application/json',
        )
        self.assertEqual(login.status_code, 200)
        listing = client.get('/api/service/service-requests/')
        self.assertEqual(listing.status_code, 200)
        body = listing.content.decode()
        self.assertNotIn('access_token', body)
        self.assertNotIn(str(req.access_token), body)
        self.assertNotIn('/service-request/result/', body)

    def test_sitemap_does_not_list_service_request_results(self):
        req = _request()
        self.assertFalse(any('service-request/result' in path for path in STATIC_SITEMAP_PATHS))
        static_map = self.client.get('/sitemap-static.xml')
        product_map = self.client.get('/sitemap-products.xml')
        index_map = self.client.get('/sitemap.xml')
        combined = (
            static_map.content.decode()
            + product_map.content.decode()
            + index_map.content.decode()
        )
        self.assertNotIn('/service-request/result/', combined)
        self.assertNotIn(str(req.access_token), combined)
