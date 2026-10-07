import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import SellerProfile
from core.models import Seller
from service_requests.models import ServiceMatch, ServiceRequest, ServiceSeller

User = get_user_model()

LOGIN_URL = '/api/service/service-seller-login/'
AUTH_ERROR = 'Неверный WhatsApp или пароль'
PHONE = '77771234567'


def _seller(**overrides):
    payload = {
        'name': 'Service STO',
        'whatsapp': PHONE,
        'password': make_password('LEGACY-PASS'),
        'city': 'Алматы',
        'seller_type': 'sto',
    }
    payload.update(overrides)
    return ServiceSeller.objects.create(**payload)


def _post_login(client, whatsapp, password):
    return client.post(
        LOGIN_URL,
        data=json.dumps({'whatsapp': whatsapp, 'password': password}),
        content_type='application/json',
    )


def _assert_authenticated_as(test, client, user):
    follow = client.get('/service-request/cabinet/')
    test.assertEqual(follow.status_code, 200)
    test.assertTrue(follow.wsgi_request.user.is_authenticated)
    test.assertEqual(follow.wsgi_request.user.pk, user.pk)


def _assert_anonymous(test, client):
    follow = client.get('/service-request/cabinet/')
    test.assertEqual(follow.status_code, 200)
    test.assertFalse(follow.wsgi_request.user.is_authenticated)


class LinkedServiceSellerLoginTests(TestCase):
    def test_correct_user_password_opens_compatible_session(self):
        user = User.objects.create_user(username='linked-sto-user', password='USER-PASS')
        seller = _seller(user=user, password=make_password('LEGACY-PASS'))
        client = Client()

        response = _post_login(client, seller.whatsapp, 'USER-PASS')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'seller_id': seller.id})
        self.assertNotIn('access_token', response.json())
        _assert_authenticated_as(self, client, user)
        seller.refresh_from_db()
        self.assertEqual(seller.password, '')

    def test_wrong_user_password_fails(self):
        user = User.objects.create_user(username='linked-sto-wrong', password='USER-PASS')
        seller = _seller(user=user)
        client = Client()

        response = _post_login(client, PHONE, 'WRONG-PASS')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': AUTH_ERROR})
        _assert_anonymous(self, client)
        seller.refresh_from_db()
        self.assertEqual(seller.user_id, user.id)

    def test_legacy_password_is_not_fallback_for_linked_seller(self):
        user = User.objects.create_user(username='linked-sto-legacy', password='USER-PASS')
        seller = _seller(user=user, password=make_password('LEGACY-PASS'))
        client = Client()

        response = _post_login(client, PHONE, 'LEGACY-PASS')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': AUTH_ERROR})
        _assert_anonymous(self, client)
        seller.refresh_from_db()
        self.assertEqual(seller.user_id, user.id)
        self.assertNotEqual(seller.password, '')

    def test_inactive_user_cannot_login(self):
        user = User.objects.create_user(
            username='linked-sto-inactive',
            password='USER-PASS',
            is_active=False,
        )
        seller = _seller(user=user)
        client = Client()

        response = _post_login(client, PHONE, 'USER-PASS')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': AUTH_ERROR})
        _assert_anonymous(self, client)
        seller.refresh_from_db()
        self.assertEqual(seller.user_id, user.id)


class LegacyServiceSellerMigrationTests(TestCase):
    def test_first_login_creates_one_user_without_password_validators(self):
        seller = _seller(password=make_password('secret'), user=None)
        client = Client()

        response = _post_login(client, '87771234567', 'secret')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'seller_id': seller.id})
        self.assertEqual(User.objects.count(), 1)
        user = User.objects.get()
        self.assertEqual(user.username, PHONE)
        self.assertTrue(user.check_password('secret'))
        seller.refresh_from_db()
        self.assertEqual(seller.user_id, user.id)
        self.assertEqual(seller.password, '')
        _assert_authenticated_as(self, client, user)
        self.assertEqual(SellerProfile.objects.count(), 0)
        self.assertEqual(Seller.objects.count(), 0)

    def test_second_login_does_not_create_another_user(self):
        seller = _seller(user=None)
        client = Client()
        self.assertEqual(_post_login(client, PHONE, 'LEGACY-PASS').status_code, 200)
        self.assertEqual(User.objects.count(), 1)

        again = Client()
        response = _post_login(again, '+7 777 123 45 67', 'LEGACY-PASS')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)
        seller.refresh_from_db()
        _assert_authenticated_as(self, again, seller.user)

    def test_after_linking_only_user_password_works(self):
        seller = _seller(user=None)
        first = Client()
        self.assertEqual(_post_login(first, PHONE, 'LEGACY-PASS').status_code, 200)
        seller.refresh_from_db()
        seller.user.set_password('NEW-PASS')
        seller.user.save(update_fields=['password'])
        seller.password = make_password('LEGACY-PASS')
        seller.save(update_fields=['password'])

        legacy = Client()
        failed = _post_login(legacy, PHONE, 'LEGACY-PASS')
        self.assertEqual(failed.status_code, 400)
        _assert_anonymous(self, legacy)

        current = Client()
        succeeded = _post_login(current, PHONE, 'NEW-PASS')
        self.assertEqual(succeeded.status_code, 200)
        _assert_authenticated_as(self, current, seller.user)
        seller.refresh_from_db()
        self.assertEqual(seller.password, '')


class ExistingUserServiceSellerLoginTests(TestCase):
    def test_legacy_password_cannot_capture_existing_user(self):
        user = User.objects.create_user(username=PHONE, password='USER-PASS')
        seller = _seller(user=None, password=make_password('LEGACY-PASS'))
        client = Client()

        response = _post_login(client, PHONE, 'LEGACY-PASS')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': AUTH_ERROR})
        _assert_anonymous(self, client)
        seller.refresh_from_db()
        self.assertIsNone(seller.user_id)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(User.objects.get().pk, user.pk)

    def test_existing_user_password_links_without_new_user(self):
        user = User.objects.create_user(username=PHONE, password='USER-PASS')
        seller = _seller(user=None, password=make_password('LEGACY-PASS'))
        client = Client()

        response = _post_login(client, '87771234567', 'USER-PASS')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'seller_id': seller.id})
        self.assertEqual(User.objects.count(), 1)
        seller.refresh_from_db()
        self.assertEqual(seller.user_id, user.id)
        self.assertEqual(seller.password, '')
        _assert_authenticated_as(self, client, user)

    def test_existing_marketplace_roles_stay_unchanged(self):
        user = User.objects.create_user(username=PHONE, password='USER-PASS')
        profile = SellerProfile.objects.create(user=user, name='Market', phone=PHONE)
        core_seller = Seller.objects.create(
            user=user,
            name='Parts',
            whatsapp=PHONE,
            transport_type='car',
        )
        seller = _seller(user=None)
        client = Client()

        response = _post_login(client, PHONE, 'USER-PASS')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SellerProfile.objects.count(), 1)
        self.assertEqual(Seller.objects.count(), 1)
        profile.refresh_from_db()
        core_seller.refresh_from_db()
        seller.refresh_from_db()
        self.assertEqual(profile.user_id, user.id)
        self.assertEqual(profile.name, 'Market')
        self.assertEqual(core_seller.user_id, user.id)
        self.assertEqual(core_seller.name, 'Parts')
        self.assertEqual(seller.user_id, user.id)

    def test_user_already_linked_to_another_service_seller_returns_400(self):
        user = User.objects.create_user(username=PHONE, password='USER-PASS')
        _seller(whatsapp='77009990001', user=user, password='')
        seller = _seller(whatsapp=PHONE, user=None, password=make_password('LEGACY-PASS'))
        client = Client()

        response = _post_login(client, PHONE, 'USER-PASS')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': AUTH_ERROR})
        seller.refresh_from_db()
        self.assertIsNone(seller.user_id)
        _assert_anonymous(self, client)

    def test_create_user_integrity_error_is_generic_auth_failure(self):
        seller = _seller(user=None)
        client = Client()

        with patch(
            'service_requests.services.service_seller_identity.User.objects.create_user',
            side_effect=IntegrityError,
        ):
            response = _post_login(client, PHONE, 'LEGACY-PASS')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': AUTH_ERROR})
        seller.refresh_from_db()
        self.assertIsNone(seller.user_id)
        self.assertEqual(User.objects.count(), 0)
        _assert_anonymous(self, client)


class ServiceSellerAmbiguityTests(TestCase):
    def test_multiple_users_for_one_phone_do_not_link(self):
        User.objects.create_user(username=PHONE, password='USER-PASS')
        User.objects.create_user(username='87771234567', password='LEGACY-PASS')
        seller = _seller(user=None, password=make_password('LEGACY-PASS'))
        client = Client()

        response = _post_login(client, PHONE, 'LEGACY-PASS')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': AUTH_ERROR})
        serialized = json.dumps(response.json(), ensure_ascii=False)
        self.assertNotIn('87771234567', serialized)
        self.assertNotIn('seller_profile', serialized)
        seller.refresh_from_db()
        self.assertIsNone(seller.user_id)
        self.assertEqual(User.objects.count(), 2)

    def test_two_raw_service_sellers_for_one_normalized_phone_do_not_pick_first(self):
        first = _seller(whatsapp=PHONE, user=None)
        second = _seller(whatsapp='87771234567', user=None)
        client = Client()

        response = _post_login(client, '+7 777 123 45 67', 'LEGACY-PASS')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': AUTH_ERROR})
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.user_id)
        self.assertIsNone(second.user_id)
        self.assertEqual(User.objects.count(), 0)


class ServiceSellerPhoneNormalizationTests(TestCase):
    def test_kz_formats_resolve_to_the_same_seller(self):
        seller = _seller(whatsapp=PHONE, user=None)
        for raw in ('+7 777 123 45 67', '87771234567', '77771234567'):
            client = Client()
            response = _post_login(client, raw, 'LEGACY-PASS')
            self.assertEqual(response.status_code, 200, raw)
            self.assertEqual(response.json()['seller_id'], seller.id)

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(User.objects.get().username, PHONE)

    def test_foreign_number_stays_international(self):
        foreign = '998901234567'
        seller = _seller(whatsapp=foreign, user=None, password=make_password('LEGACY-PASS'))
        client = Client()

        response = _post_login(client, '+998901234567', 'LEGACY-PASS')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['seller_id'], seller.id)
        user = User.objects.get()
        self.assertEqual(user.username, foreign)
        self.assertFalse(user.username.startswith('7'))


class ServiceSellerAuthRegressionTests(TestCase):
    def test_registration_stays_legacy_until_first_login(self):
        client = Client()
        response = client.post(
            '/api/service/create-service-seller/',
            data=json.dumps({
                'name': 'New STO',
                'whatsapp': PHONE,
                'password': 'Zpt-seller-1a',
                'city': 'Алматы',
                'seller_type': 'sto',
                'services': [],
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        seller = ServiceSeller.objects.get(pk=response.json()['seller_id'])
        self.assertIsNone(seller.user_id)
        self.assertEqual(User.objects.count(), 0)

    def test_create_service_request_returns_tokenized_result_url(self):
        client = Client()
        response = client.post(
            '/api/service/create-service-request/',
            data=json.dumps({
                'service_type': 'sto',
                'city': 'Кокшетау',
                'district': '',
                'phone': PHONE,
                'services': ['Диагностика'],
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        req = ServiceRequest.objects.get(pk=data['request_id'])
        self.assertEqual(
            data['result_url'],
            reverse(
                'service_request_result_page',
                kwargs={
                    'request_id': req.id,
                    'access_token': req.access_token,
                },
            ),
        )
        self.assertNotIn('access_token', data)
        page = client.get(data['result_url'])
        self.assertEqual(page.status_code, 200)
        legacy = client.get(f'/service-request/result/{req.id}/')
        self.assertEqual(legacy.status_code, 404)

    def test_private_apis_require_session_and_ignore_seller_id(self):
        seller = _seller(user=None)
        req = ServiceRequest.objects.create(
            service_type='sto',
            city='Алматы',
            phone=PHONE,
        )
        match = ServiceMatch.objects.create(request=req, seller=seller, status='new')
        client = Client()

        profile = client.get('/api/service/service-seller-profile/', {'seller_id': seller.id})
        self.assertEqual(profile.status_code, 401)
        self.assertNotIn('access_token', profile.content.decode())

        listing = client.get('/api/service/service-requests/', {'seller_id': seller.id})
        self.assertEqual(listing.status_code, 401)

        status = client.post(
            '/api/service/update-service-match-status/',
            data=json.dumps({
                'seller_id': seller.id,
                'request_id': req.id,
                'status': 'in_work',
            }),
            content_type='application/json',
        )
        self.assertEqual(status.status_code, 401)
        match.refresh_from_db()
        self.assertEqual(match.status, 'new')


PROFILE_URL = '/api/service/update-service-seller-profile/'


def _post_profile(client, payload):
    return client.post(
        PROFILE_URL,
        data=json.dumps(payload),
        content_type='application/json',
    )


class LinkedServiceSellerPasswordUpdateGuardTests(TestCase):
    def test_authenticated_seller_changes_user_password_and_keeps_session(self):
        user = User.objects.create_user(username='linked-profile-user', password='OLD-PASS-1')
        seller = _seller(user=user, password='', name='Original STO', city='Алматы')
        client = Client()
        self.assertEqual(_post_login(client, seller.whatsapp, 'OLD-PASS-1').status_code, 200)

        response = _post_profile(client, {
            'seller_id': 999999,
            'name': 'Renamed STO',
            'password': 'NEW-PASS-9',
            'services': [],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True})
        user.refresh_from_db()
        seller.refresh_from_db()
        self.assertTrue(user.check_password('NEW-PASS-9'))
        self.assertFalse(user.check_password('OLD-PASS-1'))
        self.assertEqual(seller.password, '')
        self.assertEqual(seller.name, 'Renamed STO')
        _assert_authenticated_as(self, client, user)
        self.assertEqual(_post_login(Client(), seller.whatsapp, 'OLD-PASS-1').status_code, 400)
        self.assertEqual(_post_login(Client(), seller.whatsapp, 'NEW-PASS-9').status_code, 200)

    def test_anonymous_cannot_change_linked_user_password(self):
        user = User.objects.create_user(username='attacker-target-user', password='USER-PASS')
        seller = _seller(user=user, password='', name='Original STO')
        client = Client()

        response = _post_profile(client, {
            'seller_id': seller.id,
            'name': 'Taken Over',
            'password': 'NEW-PASS-9',
        })

        self.assertEqual(response.status_code, 401)
        user.refresh_from_db()
        seller.refresh_from_db()
        self.assertTrue(user.check_password('USER-PASS'))
        self.assertFalse(user.check_password('NEW-PASS-9'))
        self.assertEqual(seller.password, '')
        self.assertEqual(seller.name, 'Original STO')

    def test_linked_seller_updates_profile_when_password_blank(self):
        user = User.objects.create_user(username='linked-blank-password', password='USER-PASS')
        seller = _seller(user=user, password='', name='Original STO')
        client = Client()
        self.assertEqual(_post_login(client, seller.whatsapp, 'USER-PASS').status_code, 200)

        response = _post_profile(client, {
            'name': 'Updated STO',
            'password': '',
            'services': [],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True})
        user.refresh_from_db()
        seller.refresh_from_db()
        self.assertEqual(seller.name, 'Updated STO')
        self.assertEqual(seller.password, '')
        self.assertTrue(user.check_password('USER-PASS'))

    def test_invalid_password_does_not_save_profile_fields(self):
        user = User.objects.create_user(username='linked-invalid-password', password='USER-PASS')
        seller = _seller(user=user, password='', name='Original STO')
        client = Client()
        self.assertEqual(_post_login(client, seller.whatsapp, 'USER-PASS').status_code, 200)

        response = _post_profile(client, {
            'name': 'Should Not Save',
            'password': 'short',
            'services': [],
        })

        self.assertEqual(response.status_code, 400)
        seller.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(seller.name, 'Original STO')
        self.assertTrue(user.check_password('USER-PASS'))
        self.assertEqual(seller.password, '')

    def test_anonymous_cannot_write_legacy_password(self):
        seller = _seller(user=None, password=make_password('LEGACY-PASS'), name='Original STO')
        response = _post_profile(Client(), {
            'seller_id': seller.id,
            'name': 'Renamed STO',
            'password': 'Fresh-pass-1',
            'services': [],
        })

        self.assertEqual(response.status_code, 401)
        seller.refresh_from_db()
        self.assertIsNone(seller.user_id)
        self.assertEqual(seller.name, 'Original STO')
        self.assertTrue(check_password('LEGACY-PASS', seller.password))
        self.assertFalse(check_password('Fresh-pass-1', seller.password))
        self.assertEqual(
            _post_login(Client(), seller.whatsapp, 'LEGACY-PASS').status_code,
            200,
        )
