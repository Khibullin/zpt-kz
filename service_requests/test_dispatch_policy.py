import json
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase

from service_requests.models import (
    Service,
    ServiceBroadcastSettings,
    ServiceMatch,
    ServiceRequest,
    ServiceRequestDispatch,
    ServiceSeller,
)
from service_requests.services.dispatch_policy import select_service_sellers_for_request
from service_requests.views import match_services


class DispatchPolicyTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = '/api/service/create-service-request/'

    def set_mode(self, mode):
        ServiceBroadcastSettings.objects.create(mode=mode)

    def add_service(self, name):
        return Service.objects.create(name=name)

    def make_seller(self, name, whatsapp, services=(), **overrides):
        payload = {
            'name': name,
            'whatsapp': whatsapp,
            'password': make_password('secret'),
            'city': 'Алматы',
            'district': 'Бостандыкский',
            'seller_type': 'sto',
            'is_active': True,
            'receive_requests': True,
            'is_paused': False,
            'is_test_seller': False,
            'dispatch_priority': 1000,
        }
        payload.update(overrides)
        seller = ServiceSeller.objects.create(**payload)
        if services:
            seller.services.add(*services)
        return seller

    def make_request(self, services, **overrides):
        payload = {
            'service_type': 'sto',
            'city': 'Алматы',
            'district': 'Бостандыкский',
            'phone': '77001234567',
        }
        payload.update(overrides)
        req = ServiceRequest.objects.create(**payload)
        req.services.add(*services)
        return req

    def post_request(self, **overrides):
        payload = {
            'service_type': 'sto',
            'city': 'Алматы',
            'district': 'Бостандыкский',
            'phone': '77001234567',
            'services': ['Диагностика'],
        }
        payload.update(overrides)
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type='application/json',
        )

    def matched_pks(self, req):
        return list(
            ServiceMatch.objects.filter(request=req)
            .order_by('pk')
            .values_list('seller_id', flat=True)
        )

    def queued_seller_ids(self, req):
        return list(
            ServiceRequestDispatch.objects.filter(request=req)
            .order_by('position_number', 'pk')
            .values_list('seller_id', flat=True)
        )


class OffModeTests(DispatchPolicyTestCase):
    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_off_saves_request_without_matches_or_sends(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_OFF)
        service = self.add_service('Диагностика')
        self.make_seller('Eligible', '77001000001', services=[service])

        response = self.post_request()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        req = ServiceRequest.objects.get(pk=data['request_id'])

        self.assertEqual(ServiceMatch.objects.filter(request=req).count(), 0)
        self.assertEqual(ServiceRequestDispatch.objects.filter(request=req).count(), 0)
        self.assertEqual(mock_send.call_count, 0)
        self.assertEqual(data['sellers_count'], 0)
        self.assertEqual(data['sellers'], [])
        self.assertNotIn('отправлена', data['title'])
        self.assertEqual(data['title'], '✅ Заявка принята.')
        self.assertIn(str(req.access_token), data['result_url'])

        page = self.client.get(data['result_url'])
        self.assertEqual(page.status_code, 200)

    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_missing_settings_fail_closed(self, mock_send):
        self.assertFalse(ServiceBroadcastSettings.objects.exists())
        service = self.add_service('Диагностика')
        seller = self.make_seller('Eligible', '77001000011', services=[service])
        req = self.make_request([service])

        matched = match_services(req)

        self.assertEqual(matched, [])
        self.assertEqual(select_service_sellers_for_request(req), [])
        self.assertEqual(ServiceMatch.objects.filter(request=req, seller=seller).count(), 0)
        self.assertEqual(ServiceRequestDispatch.objects.filter(request=req).count(), 0)
        self.assertEqual(mock_send.call_count, 0)
        self.assertFalse(ServiceBroadcastSettings.objects.exists())


class TestModeTests(DispatchPolicyTestCase):
    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_test_mode_selects_only_active_receiving_unpaused_test_seller(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_TEST)
        service = self.add_service('Диагностика')
        seller_a = self.make_seller(
            'A', '77001000021', services=[service], is_test_seller=True,
        )
        seller_b = self.make_seller(
            'B', '77001000022', services=[service], is_test_seller=False,
        )
        self.make_seller(
            'C', '77001000023', services=[service],
            is_test_seller=True, is_active=False,
        )
        self.make_seller(
            'D', '77001000024', services=[service],
            is_test_seller=True, receive_requests=False,
        )
        self.make_seller(
            'E', '77001000025', services=[service],
            is_test_seller=True, is_paused=True,
        )

        response = self.post_request()
        data = response.json()
        req = ServiceRequest.objects.get(pk=data['request_id'])

        self.assertEqual(self.matched_pks(req), [seller_a.pk])
        self.assertEqual([item['name'] for item in data['sellers']], ['A'])
        self.assertEqual(data['sellers_count'], 1)
        self.assertEqual(self.queued_seller_ids(req), [seller_a.pk])
        self.assertEqual(mock_send.call_count, 0)
        self.assertNotIn(seller_b.pk, self.matched_pks(req))


class LiveModeTests(DispatchPolicyTestCase):
    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_live_includes_test_seller_and_excludes_inactive_flags(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service('Диагностика')
        seller_a = self.make_seller(
            'A', '77001000031', services=[service], is_test_seller=True,
        )
        seller_b = self.make_seller(
            'B', '77001000032', services=[service], is_test_seller=False,
        )
        self.make_seller(
            'C', '77001000033', services=[service], is_active=False,
        )
        self.make_seller(
            'D', '77001000034', services=[service], receive_requests=False,
        )
        self.make_seller(
            'E', '77001000035', services=[service], is_paused=True,
        )

        req = self.make_request([service])
        matched = match_services(req)

        self.assertEqual([seller.pk for seller in matched], [seller_a.pk, seller_b.pk])
        self.assertEqual(self.queued_seller_ids(req), [seller_a.pk, seller_b.pk])
        self.assertEqual(mock_send.call_count, 0)
        self.assertEqual(
            set(ServiceMatch.objects.filter(request=req).values_list('status', flat=True)),
            {'new'},
        )


class DistrictFallbackTests(DispatchPolicyTestCase):
    def _pair(self, service, **seller_a):
        self.set_mode(seller_a.pop('mode', ServiceBroadcastSettings.MODE_LIVE))
        district_seller = self.make_seller(
            'A', '77001000041', services=[service], district='Бостандыкский', **seller_a,
        )
        city_seller = self.make_seller(
            'B', '77001000042', services=[service], district='Ауэзовский',
            is_test_seller=seller_a.get('is_test_seller', False),
        )
        return district_seller, city_seller

    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_eligible_district_seller_excludes_other_district(self, mock_send):
        service = self.add_service('Диагностика')
        district_seller, city_seller = self._pair(service)
        req = self.make_request([service], district='Бостандыкский')

        matched = match_services(req)

        self.assertEqual([seller.pk for seller in matched], [district_seller.pk])
        self.assertNotIn(city_seller.pk, [seller.pk for seller in matched])

    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_paused_district_seller_falls_back_to_city(self, mock_send):
        service = self.add_service('Диагностика')
        district_seller, city_seller = self._pair(service, is_paused=True)
        req = self.make_request([service], district='Бостандыкский')

        matched = match_services(req)

        self.assertEqual([seller.pk for seller in matched], [city_seller.pk])
        self.assertFalse(
            ServiceMatch.objects.filter(request=req, seller=district_seller).exists()
        )

    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_receive_requests_false_district_seller_falls_back_to_city(self, mock_send):
        service = self.add_service('Диагностика')
        district_seller, city_seller = self._pair(service, receive_requests=False)
        req = self.make_request([service], district='Бостандыкский')

        matched = match_services(req)

        self.assertEqual([seller.pk for seller in matched], [city_seller.pk])
        self.assertFalse(
            ServiceMatch.objects.filter(request=req, seller=district_seller).exists()
        )

    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_non_test_district_seller_falls_back_in_test_mode(self, mock_send):
        service = self.add_service('Диагностика')
        ServiceBroadcastSettings.objects.create(mode=ServiceBroadcastSettings.MODE_TEST)
        district_seller = self.make_seller(
            'A', '77001000051', services=[service],
            district='Бостандыкский', is_test_seller=False,
        )
        city_seller = self.make_seller(
            'B', '77001000052', services=[service],
            district='Ауэзовский', is_test_seller=True,
        )
        req = self.make_request([service], district='Бостандыкский')

        matched = match_services(req)

        self.assertEqual([seller.pk for seller in matched], [city_seller.pk])
        self.assertFalse(
            ServiceMatch.objects.filter(request=req, seller=district_seller).exists()
        )


class DispatchPriorityTests(DispatchPolicyTestCase):
    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_lower_priority_is_sent_first_with_pk_tie_break(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service('Диагностика')
        seller_b = self.make_seller(
            'B', '77001000061', services=[service], dispatch_priority=10,
        )
        seller_c = self.make_seller(
            'C', '77001000062', services=[service], dispatch_priority=10,
        )
        seller_a = self.make_seller(
            'A', '77001000063', services=[service], dispatch_priority=100,
        )
        self.assertLess(seller_b.pk, seller_c.pk)

        response = self.post_request()
        data = response.json()

        req = ServiceRequest.objects.get(pk=data['request_id'])
        self.assertEqual(
            self.queued_seller_ids(req),
            [seller_b.pk, seller_c.pk, seller_a.pk],
        )
        self.assertEqual(
            list(
                ServiceRequestDispatch.objects.filter(request=req)
                .order_by('position_number')
                .values_list('position_number', flat=True)
            ),
            [1, 2, 3],
        )
        self.assertEqual(mock_send.call_count, 0)
        self.assertEqual([item['name'] for item in data['sellers']], ['B', 'C', 'A'])


class ServiceIntersectionTests(DispatchPolicyTestCase):
    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_any_service_intersection_is_enough(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        diagnostics = self.add_service('Диагностика')
        chassis = self.add_service('Ходовая')
        wash = self.add_service('Мойка')
        seller_a = self.make_seller('A', '77001000071', services=[diagnostics])
        seller_b = self.make_seller('B', '77001000072', services=[wash])
        req = self.make_request([diagnostics, chassis])

        matched = match_services(req)

        self.assertEqual([seller.pk for seller in matched], [seller_a.pk])
        self.assertFalse(ServiceMatch.objects.filter(request=req, seller=seller_b).exists())


class SellerTypeAndCityTests(DispatchPolicyTestCase):
    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_seller_type_must_match_request(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service('Диагностика')
        sto = self.make_seller('STO', '77001000081', services=[service], seller_type='sto')
        detailing = self.make_seller(
            'Detail', '77001000082', services=[service], seller_type='detailing',
        )

        sto_request = self.make_request([service], service_type='sto')
        self.assertEqual(
            [seller.pk for seller in match_services(sto_request)],
            [sto.pk],
        )

        detailing_request = self.make_request([service], service_type='detailing')
        self.assertEqual(
            [seller.pk for seller in match_services(detailing_request)],
            [detailing.pk],
        )

    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_other_city_is_excluded_even_with_better_priority(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service('Диагностика')
        local = self.make_seller(
            'Local', '77001000091', services=[service],
            city='Алматы', dispatch_priority=100,
        )
        other = self.make_seller(
            'Other', '77001000092', services=[service],
            city='Астана', district='', dispatch_priority=1,
        )
        req = self.make_request([service], city='Алматы')

        matched = match_services(req)

        self.assertEqual([seller.pk for seller in matched], [local.pk])
        self.assertFalse(ServiceMatch.objects.filter(request=req, seller=other).exists())
        self.assertEqual(self.queued_seller_ids(req), [local.pk])
        self.assertEqual(mock_send.call_count, 0)


class UnknownModeTests(DispatchPolicyTestCase):
    @patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message')
    def test_unknown_mode_fail_closed_without_error(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        ServiceBroadcastSettings.objects.filter(pk=1).update(mode='unexpected')
        service = self.add_service('Диагностика')
        self.make_seller('Eligible', '77001000101', services=[service])
        req = self.make_request([service])

        matched = match_services(req)

        self.assertEqual(matched, [])
        self.assertEqual(select_service_sellers_for_request(req), [])
        self.assertEqual(ServiceMatch.objects.filter(request=req).count(), 0)
        self.assertEqual(ServiceRequestDispatch.objects.filter(request=req).count(), 0)
        self.assertEqual(mock_send.call_count, 0)
