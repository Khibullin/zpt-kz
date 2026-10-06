import json
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from service_requests.models import ServiceMatch, ServiceRequest, ServiceSeller
from service_requests.views import build_service_request_success_payload

User = get_user_model()


def _seller(**overrides):
    payload = {
        'name': 'Legacy STO',
        'whatsapp': '77005550001',
        'password': make_password('secret'),
        'city': 'Алматы',
        'seller_type': 'sto',
    }
    payload.update(overrides)
    return ServiceSeller.objects.create(**payload)


def _request(**overrides):
    payload = {
        'service_type': 'sto',
        'city': 'Алматы',
        'phone': '77001234567',
    }
    payload.update(overrides)
    return ServiceRequest.objects.create(**payload)


class ServiceSellerUserFoundationTests(TestCase):
    def test_seller_can_be_created_without_user(self):
        seller = _seller()
        self.assertIsNone(seller.user)
        self.assertIsNone(seller.user_id)

    def test_legacy_seller_without_user_still_logs_in(self):
        seller = _seller()
        self.assertIsNone(seller.user_id)

        response = Client().post(
            '/api/service/service-seller-login/',
            data=json.dumps({'whatsapp': seller.whatsapp, 'password': 'secret'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'seller_id': seller.id})

    def test_seller_can_be_linked_to_user(self):
        user = User.objects.create_user(username='service-seller-user', password='unused-pass-1')
        seller = _seller(user=user)
        seller.refresh_from_db()

        self.assertEqual(seller.user_id, user.id)
        self.assertEqual(user.service_seller_profile, seller)

    def test_one_user_cannot_link_two_sellers(self):
        user = User.objects.create_user(username='service-seller-once', password='unused-pass-1')
        _seller(user=user, whatsapp='77005550011')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _seller(user=user, whatsapp='77005550012')

    def test_deleting_user_keeps_seller_and_clears_link(self):
        user = User.objects.create_user(username='service-seller-delete', password='unused-pass-1')
        seller = _seller(user=user)
        seller_id = seller.id

        user.delete()

        seller = ServiceSeller.objects.get(pk=seller_id)
        self.assertIsNone(seller.user)
        self.assertIsNone(seller.user_id)


class ServiceRequestAccessTokenFoundationTests(TestCase):
    def test_new_request_receives_uuid4(self):
        req = _request()
        self.assertIsInstance(req.access_token, uuid.UUID)
        self.assertEqual(req.access_token.version, 4)

    def test_two_requests_receive_different_tokens(self):
        first = _request(phone='77001000001')
        second = _request(phone='77001000002')
        self.assertNotEqual(first.access_token, second.access_token)

    def test_token_is_unique_in_database(self):
        shared = uuid.uuid4()
        _request(phone='77001000003', access_token=shared)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _request(phone='77001000004', access_token=shared)

    def test_ordinary_save_does_not_change_token(self):
        req = _request()
        original = req.access_token

        req.city = 'Астана'
        req.save()
        req.refresh_from_db()

        self.assertEqual(req.access_token, original)

    def test_create_api_assigns_token_without_accepting_one(self):
        response = Client().post(
            '/api/service/create-service-request/',
            data=json.dumps({
                'service_type': 'sto',
                'city': 'Кокшетау',
                'district': '',
                'phone': '77001234567',
                'services': ['Диагностика'],
                'access_token': str(uuid.uuid4()),
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        req = ServiceRequest.objects.get(pk=data['request_id'])

        self.assertIsInstance(req.access_token, uuid.UUID)
        self.assertNotIn('access_token', data)
        self.assertEqual(
            data['result_url'],
            reverse('service_request_result_page', args=[req.id]),
        )
        self.assertNotIn(str(req.access_token), data['result_url'])


class ServiceRequestSecurityFoundationRegressionTests(TestCase):
    def test_success_payload_keeps_legacy_result_url(self):
        req = _request()
        payload = build_service_request_success_payload(req, [])

        self.assertEqual(
            payload['result_url'],
            reverse('service_request_result_page', args=[req.id]),
        )
        self.assertNotIn('access_token', payload)
        self.assertNotIn(str(req.access_token), payload['result_url'])

    def test_legacy_result_url_still_opens(self):
        req = _request()
        response = Client().get(reverse('service_request_result_page', args=[req.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Заявка №{req.id}')

    def test_private_apis_require_session(self):
        seller = _seller()
        self.assertIsNone(seller.user_id)
        req = _request()
        match = ServiceMatch.objects.create(request=req, seller=seller, status='new')
        client = Client()

        cabinet = client.get('/service-request/cabinet/')
        self.assertEqual(cabinet.status_code, 200)

        profile = client.get(
            '/api/service/service-seller-profile/',
            {'seller_id': seller.id},
        )
        self.assertEqual(profile.status_code, 401)
        self.assertNotIn(req.phone, profile.content.decode())
        self.assertNotIn('access_token', profile.content.decode())

        requests_response = client.get(
            '/api/service/service-requests/',
            {'seller_id': seller.id},
        )
        self.assertEqual(requests_response.status_code, 401)

        status_response = client.post(
            '/api/service/update-service-match-status/',
            data=json.dumps({
                'seller_id': seller.id,
                'request_id': req.id,
                'status': 'in_work',
            }),
            content_type='application/json',
        )
        self.assertEqual(status_response.status_code, 401)
        match.refresh_from_db()
        self.assertEqual(match.status, 'new')


class ServiceRequestAccessTokenMigrationTests(TransactionTestCase):
    def test_existing_rows_receive_unique_tokens_and_sellers_stay_unlinked(self):
        executor = MigrationExecutor(connection)
        old = ('service_requests', '0007_alter_service_options_and_more')
        new = ('service_requests', '0008_service_security_foundation')

        try:
            executor.migrate([old])
            executor.loader.build_graph()
            old_apps = executor.loader.project_state([old]).apps
            OldRequest = old_apps.get_model('service_requests', 'ServiceRequest')
            OldSeller = old_apps.get_model('service_requests', 'ServiceSeller')
            self.assertFalse(any(field.name == 'access_token' for field in OldRequest._meta.fields))
            self.assertFalse(any(field.name == 'user' for field in OldSeller._meta.fields))

            User.objects.create_user(username='77006660001', password='unused-pass-1')
            OldSeller.objects.create(
                name='Existing STO',
                whatsapp='77006660001',
                password='legacy-hash',
                city='Алматы',
                seller_type='sto',
            )
            OldRequest.objects.create(
                service_type='sto',
                city='Алматы',
                phone='77006660011',
            )
            OldRequest.objects.create(
                service_type='detailing',
                city='Астана',
                phone='77006660012',
            )

            executor.loader.build_graph()
            executor.migrate([new])
            executor.loader.build_graph()
            new_apps = executor.loader.project_state([new]).apps
            NewRequest = new_apps.get_model('service_requests', 'ServiceRequest')
            NewSeller = new_apps.get_model('service_requests', 'ServiceSeller')

            tokens = list(NewRequest.objects.values_list('access_token', flat=True))
            self.assertEqual(len(tokens), 2)
            self.assertTrue(all(token is not None for token in tokens))
            self.assertEqual(len(set(tokens)), 2)
            for token in tokens:
                self.assertEqual(uuid.UUID(str(token)).version, 4)

            seller = NewSeller.objects.get(whatsapp='77006660001')
            self.assertIsNone(seller.user_id)
        finally:
            executor.loader.build_graph()
            executor.migrate([new])
