import json
import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from service_requests.models import ServiceMatch, ServiceRequest, ServiceSeller

User = get_user_model()

PROFILE_URL = '/api/service/service-seller-profile/'
REQUESTS_URL = '/api/service/service-requests/'
UPDATE_PROFILE_URL = '/api/service/update-service-seller-profile/'
UPDATE_STATUS_URL = '/api/service/update-service-match-status/'
MARK_VIEWED_URL = '/api/service/mark-service-requests-viewed/'
LOGOUT_URL = '/api/service/service-seller-logout/'
LOGIN_URL = '/api/service/service-seller-login/'
CABINET_URL = '/service-request/cabinet/'


def _seller(**overrides):
    payload = {
        'name': 'Service STO',
        'whatsapp': '77771110001',
        'password': '',
        'city': 'Алматы',
        'seller_type': 'sto',
    }
    payload.update(overrides)
    return ServiceSeller.objects.create(**payload)


def _request(**overrides):
    payload = {
        'service_type': 'sto',
        'city': 'Алматы',
        'phone': '77000000001',
        'description': 'private request',
    }
    payload.update(overrides)
    return ServiceRequest.objects.create(**payload)


def _login(client, whatsapp, password='USER-PASS'):
    return client.post(
        LOGIN_URL,
        data=json.dumps({'whatsapp': whatsapp, 'password': password}),
        content_type='application/json',
    )


def _post(client, url, payload):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type='application/json',
    )


def _cabinet_token(client):
    page = client.get(CABINET_URL, secure=True)
    match = re.search(br'name="csrfmiddlewaretoken" value="([^"]+)"', page.content)
    return page, match


def _csrf_post(client, token, url, payload):
    body = '{}' if payload is None else json.dumps(payload)
    return client.post(
        url,
        data=body,
        content_type='application/json',
        HTTP_X_CSRFTOKEN=token,
        HTTP_ORIGIN='https://testserver',
        secure=True,
    )


class ServiceSellerPrivateApiTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='service-user-a', password='USER-PASS')
        self.user_b = User.objects.create_user(username='service-user-b', password='USER-PASS')
        self.seller_a = _seller(
            name='Seller A',
            whatsapp='77771110001',
            user=self.user_a,
            city='Алматы',
        )
        self.seller_b = _seller(
            name='Seller B',
            whatsapp='77771110002',
            user=self.user_b,
            city='Астана',
        )
        self.request_a = _request(phone='77000000011', description='only A')
        self.request_b = _request(phone='77000000022', description='only B secret')
        self.match_a = ServiceMatch.objects.create(
            request=self.request_a,
            seller=self.seller_a,
            status='new',
        )
        self.match_b = ServiceMatch.objects.create(
            request=self.request_b,
            seller=self.seller_b,
            status='new',
        )

    def test_anonymous_private_reads_are_401(self):
        client = Client()
        profile = client.get(PROFILE_URL, {'seller_id': self.seller_b.id})
        listing = client.get(REQUESTS_URL, {'seller_id': self.seller_b.id})
        self.assertEqual(profile.status_code, 401)
        self.assertEqual(listing.status_code, 401)
        self.assertNotIn('only B secret', profile.content.decode() + listing.content.decode())

    def test_anonymous_mutations_are_rejected(self):
        client = Client()
        profile = _post(client, UPDATE_PROFILE_URL, {
            'seller_id': self.seller_a.id,
            'name': 'Hacked',
            'password': 'NEW-PASS-9',
        })
        status = _post(client, UPDATE_STATUS_URL, {
            'seller_id': self.seller_a.id,
            'request_id': self.request_a.id,
            'status': 'done',
        })
        self.assertEqual(profile.status_code, 401)
        self.assertEqual(status.status_code, 401)
        self.seller_a.refresh_from_db()
        self.user_a.refresh_from_db()
        self.match_a.refresh_from_db()
        self.assertEqual(self.seller_a.name, 'Seller A')
        self.assertTrue(self.user_a.check_password('USER-PASS'))
        self.assertEqual(self.match_a.status, 'new')

    def test_authenticated_user_without_service_seller_is_forbidden(self):
        user = User.objects.create_user(username='no-service-seller', password='USER-PASS')
        before = ServiceSeller.objects.count()
        client = Client()
        client.force_login(user)

        profile = client.get(PROFILE_URL)
        listing = client.get(REQUESTS_URL)
        update = _post(client, UPDATE_PROFILE_URL, {'name': 'Nope'})
        status = _post(client, UPDATE_STATUS_URL, {
            'request_id': self.request_a.id,
            'status': 'done',
        })

        self.assertEqual(profile.status_code, 403)
        self.assertEqual(listing.status_code, 403)
        self.assertEqual(update.status_code, 403)
        self.assertEqual(status.status_code, 403)
        self.assertEqual(ServiceSeller.objects.count(), before)
        self.match_a.refresh_from_db()
        self.assertEqual(self.match_a.status, 'new')

    def test_profile_seller_id_spoof_reads_and_updates_only_self(self):
        client = Client()
        self.assertEqual(_login(client, self.seller_a.whatsapp).status_code, 200)

        profile = client.get(PROFILE_URL, {'seller_id': self.seller_b.id})
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()['id'], self.seller_a.id)
        self.assertEqual(profile.json()['name'], 'Seller A')
        self.assertNotIn('Seller B', profile.content.decode())

        update = _post(client, UPDATE_PROFILE_URL, {
            'seller_id': self.seller_b.id,
            'name': 'A renamed',
            'city': 'Шымкент',
            'services': [],
        })
        self.assertEqual(update.status_code, 200)
        self.seller_a.refresh_from_db()
        self.seller_b.refresh_from_db()
        self.assertEqual(self.seller_a.name, 'A renamed')
        self.assertEqual(self.seller_a.city, 'Шымкент')
        self.assertEqual(self.seller_b.name, 'Seller B')
        self.assertEqual(self.seller_b.city, 'Астана')

    def test_request_list_ignores_seller_id_and_hides_other_seller(self):
        client = Client()
        self.assertEqual(_login(client, self.seller_a.whatsapp).status_code, 200)

        listing = client.get(REQUESTS_URL, {'seller_id': self.seller_b.id})
        self.assertEqual(listing.status_code, 200)
        items = listing.json()['requests']
        self.assertEqual([item['id'] for item in items], [self.request_a.id])
        self.assertEqual(items[0]['status'], 'new')
        self.assertEqual(items[0]['description'], 'only A')
        body = listing.content.decode()
        self.assertNotIn('only B secret', body)
        self.assertNotIn('77000000022', body)
        self.assertNotIn('access_token', body)
        self.match_a.refresh_from_db()
        self.match_b.refresh_from_db()
        self.assertEqual(self.match_a.status, 'new')
        self.assertEqual(self.match_b.status, 'new')

    def test_status_update_cannot_touch_another_sellers_match(self):
        client = Client()
        self.assertEqual(_login(client, self.seller_a.whatsapp).status_code, 200)

        response = _post(client, UPDATE_STATUS_URL, {
            'seller_id': self.seller_b.id,
            'request_id': self.request_b.id,
            'status': 'done',
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'error': 'Match not found'})
        self.match_b.refresh_from_db()
        self.assertEqual(self.match_b.status, 'new')

        own = _post(client, UPDATE_STATUS_URL, {
            'seller_id': self.seller_b.id,
            'request_id': self.request_a.id,
            'status': 'in_work',
        })
        self.assertEqual(own.status_code, 200)
        self.match_a.refresh_from_db()
        self.assertEqual(self.match_a.status, 'in_work')

    def test_mark_viewed_updates_only_own_new_matches(self):
        ServiceMatch.objects.create(
            request=_request(phone='77000000033', description='A already viewed'),
            seller=self.seller_a,
            status='in_work',
        )
        client = Client()
        self.assertEqual(_login(client, self.seller_a.whatsapp).status_code, 200)
        self.assertEqual(client.get(REQUESTS_URL).status_code, 200)
        self.match_a.refresh_from_db()
        self.match_b.refresh_from_db()
        self.assertEqual(self.match_a.status, 'new')
        self.assertEqual(self.match_b.status, 'new')

        marked = _post(client, MARK_VIEWED_URL, {})
        self.assertEqual(marked.status_code, 200)
        self.match_a.refresh_from_db()
        self.match_b.refresh_from_db()
        self.assertEqual(self.match_a.status, 'viewed')
        self.assertEqual(self.match_b.status, 'new')
        self.assertEqual(
            ServiceMatch.objects.get(seller=self.seller_a, status='in_work').status,
            'in_work',
        )

    def test_password_change_does_not_affect_other_seller(self):
        client = Client()
        self.assertEqual(_login(client, self.seller_a.whatsapp).status_code, 200)
        response = _post(client, UPDATE_PROFILE_URL, {
            'seller_id': self.seller_b.id,
            'password': 'NEW-PASS-9',
            'services': [],
        })
        self.assertEqual(response.status_code, 200)
        self.user_a.refresh_from_db()
        self.user_b.refresh_from_db()
        self.seller_a.refresh_from_db()
        self.seller_b.refresh_from_db()
        self.assertTrue(self.user_a.check_password('NEW-PASS-9'))
        self.assertFalse(self.user_a.check_password('USER-PASS'))
        self.assertTrue(self.user_b.check_password('USER-PASS'))
        self.assertEqual(self.seller_a.password, '')
        self.assertEqual(self.seller_b.password, '')
        self.assertEqual(self.seller_b.name, 'Seller B')
        follow = client.get(CABINET_URL)
        self.assertTrue(follow.wsgi_request.user.is_authenticated)
        self.assertEqual(follow.wsgi_request.user.pk, self.user_a.pk)


class ServiceSellerLogoutTests(TestCase):
    def test_logout_destroys_session_and_can_repeat(self):
        user = User.objects.create_user(username='logout-user', password='USER-PASS')
        seller = _seller(user=user, whatsapp='77771110009')
        client = Client()
        self.assertEqual(_login(client, seller.whatsapp).status_code, 200)
        self.assertEqual(client.get(PROFILE_URL).status_code, 200)

        first = _post(client, LOGOUT_URL, {})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(client.get(PROFILE_URL).status_code, 401)
        self.assertFalse(client.get(CABINET_URL).wsgi_request.user.is_authenticated)

        second = _post(client, LOGOUT_URL, {})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(client.get(PROFILE_URL).status_code, 401)


class ServiceSellerCsrfTests(TestCase):
    def test_mutating_endpoints_reject_missing_csrf_token(self):
        user = User.objects.create_user(username='csrf-user', password='USER-PASS')
        seller = _seller(user=user, whatsapp='77771110008', password='')
        client = Client(enforce_csrf_checks=True)

        login = client.post(
            LOGIN_URL,
            data=json.dumps({'whatsapp': seller.whatsapp, 'password': 'USER-PASS'}),
            content_type='application/json',
            secure=True,
        )
        self.assertEqual(login.status_code, 403)

        page, match = _cabinet_token(client)
        self.assertEqual(page.status_code, 200)
        self.assertIsNotNone(match)
        token = match.group(1).decode()

        allowed = _csrf_post(client, token, LOGIN_URL, {
            'whatsapp': seller.whatsapp,
            'password': 'USER-PASS',
        })
        self.assertEqual(allowed.status_code, 200)

        for url, payload in (
            (UPDATE_PROFILE_URL, {'name': 'Nope', 'services': []}),
            (UPDATE_STATUS_URL, {'request_id': 1, 'status': 'done'}),
            (MARK_VIEWED_URL, {}),
            (LOGOUT_URL, {}),
        ):
            blocked = client.post(
                url,
                data=json.dumps(payload),
                content_type='application/json',
                HTTP_ORIGIN='https://testserver',
                secure=True,
            )
            self.assertEqual(blocked.status_code, 403, url)


class ServiceSellerCabinetFrontendTests(TestCase):
    def test_cabinet_drops_localstorage_identity_and_uses_real_routes(self):
        response = Client().get(CABINET_URL)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('localStorage.removeItem(\'seller_id\')', content)
        self.assertNotIn('localStorage.getItem(\'seller_id\')', content)
        self.assertNotIn('localStorage.setItem(\'seller_id\'', content)
        self.assertIn('/service-requests/', content)
        self.assertIn('/update-service-match-status/', content)
        self.assertIn('/mark-service-requests-viewed/', content)
        self.assertIn('/service-seller-logout/', content)
        self.assertIn('csrfmiddlewaretoken', content)
        self.assertIn('X-CSRFToken', content)
        self.assertNotIn('/get-requests/', content)
        self.assertNotIn('/update-match-status/', content)
