import json
import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management import CommandError, call_command
from django.test import Client, TestCase
from django.utils import timezone

from service_requests.admin import ServiceRequestDispatchAdmin
from service_requests.models import (
    Service,
    ServiceBroadcastSettings,
    ServiceMatch,
    ServiceRequest,
    ServiceRequestDispatch,
    ServiceSeller,
    ServiceWhatsAppMessageLog,
)
from service_requests.services.whatsapp_dispatch import (
    SKIP_BROADCAST_DISABLED,
    SKIP_NOT_TEST_SELLER,
    SKIP_RECEIVE_REQUESTS_DISABLED,
    SKIP_SELLER_INACTIVE,
    SKIP_SELLER_PAUSED,
    process_due_service_request_dispatches,
)
from service_requests.views import match_services
from django.contrib import admin


SENDER = 'service_requests.services.whatsapp_dispatch.send_whatsapp_template_message'


class WhatsAppDispatchTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = '/api/service/create-service-request/'

    def set_mode(self, mode):
        ServiceBroadcastSettings.objects.create(mode=mode)

    def add_service(self, name='Диагностика'):
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
            'brand': 'Toyota',
            'model': 'Camry',
            'description': 'Стук в ходовой',
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
            'brand': 'Toyota',
            'model': 'Camry',
            'description': 'Стук в ходовой',
            'services': ['Диагностика'],
        }
        payload.update(overrides)
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type='application/json',
        )

def _ok_result(message_id='wamid.test'):
    return {
        'ok': True,
        'message_id': message_id,
        'response': {'messages': [{'id': message_id}]},
        'error': None,
    }


def _fail_result(error='temporary outage'):
    return {
        'ok': False,
        'message_id': '',
        'error': error,
        'response': '',
    }


class CreateRequestOutboxTests(WhatsAppDispatchTestCase):
    @patch(SENDER)
    def test_post_queues_without_network_send(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service()
        seller = self.make_seller('A', '77002000001', services=[service])

        response = self.post_request()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        req = ServiceRequest.objects.get(pk=data['request_id'])
        self.assertTrue(ServiceMatch.objects.filter(request=req, seller=seller).exists())
        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_QUEUED)
        self.assertEqual(dispatch.attempts_count, 0)
        self.assertEqual(mock_send.call_count, 0)
        self.assertNotIn('отправлена', data['title'])
        self.assertNotIn('отправлена', data['message'])
        self.assertNotIn('skip_reason', data)
        self.assertEqual(
            ServiceMatch.objects.get(request=req, seller=seller).status,
            'new',
        )

        page = self.client.get(data['result_url'])
        content = page.content.decode('utf-8')
        self.assertIn('Подходящие исполнители', content)
        self.assertIn('Мы уведомим их в WhatsApp', content)
        self.assertNotIn('отправлена исполнителям', content)
        self.assertNotIn('Исполнители, которым отправлена заявка', content)
        self.assertNotIn('Отправлено:', content)

    @patch(SENDER)
    def test_outbox_follows_priority_and_excludes_ineligible(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service()
        seller_b = self.make_seller(
            'B', '77002000011', services=[service], dispatch_priority=10,
        )
        seller_c = self.make_seller(
            'C', '77002000012', services=[service], dispatch_priority=10,
        )
        seller_a = self.make_seller(
            'A', '77002000013', services=[service], dispatch_priority=100,
        )
        excluded = self.make_seller(
            'Paused', '77002000014', services=[service], is_paused=True,
            dispatch_priority=1,
        )
        self.assertLess(seller_b.pk, seller_c.pk)
        req = self.make_request([service])

        match_services(req)
        match_services(req)

        rows = list(
            ServiceRequestDispatch.objects.filter(request=req)
            .order_by('position_number')
        )
        self.assertEqual(
            [(row.seller_id, row.position_number) for row in rows],
            [(seller_b.pk, 1), (seller_c.pk, 2), (seller_a.pk, 3)],
        )
        self.assertEqual(ServiceRequestDispatch.objects.filter(request=req).count(), 3)
        self.assertFalse(
            ServiceRequestDispatch.objects.filter(request=req, seller=excluded).exists()
        )
        self.assertEqual(mock_send.call_count, 0)

    @patch(SENDER)
    def test_off_creates_no_dispatch(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_OFF)
        service = self.add_service()
        self.make_seller('A', '77002000021', services=[service])
        req = self.make_request([service])

        match_services(req)

        self.assertEqual(ServiceRequestDispatch.objects.filter(request=req).count(), 0)
        self.assertEqual(mock_send.call_count, 0)

    @patch('service_requests.services.whatsapp_dispatch.ServiceRequestDispatch.objects.get_or_create')
    def test_enqueue_failure_rolls_back_request_and_match(self, get_or_create):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service()
        self.make_seller('A', '77002000031', services=[service])
        get_or_create.side_effect = RuntimeError('dispatch write failed')

        with self.assertRaises(RuntimeError):
            self.post_request()

        self.assertEqual(ServiceRequest.objects.count(), 0)
        self.assertEqual(ServiceMatch.objects.count(), 0)
        self.assertEqual(ServiceRequestDispatch.objects.count(), 0)


class WorkerDeliveryTests(WhatsAppDispatchTestCase):
    def _queued(self):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service()
        seller = self.make_seller('A', '77002000041', services=[service])
        req = self.make_request([service])
        match_services(req)
        return req, seller

    @patch(SENDER)
    def test_successful_send_marks_sent_and_writes_one_log(self, mock_send):
        mock_send.return_value = _ok_result()
        req, seller = self._queued()

        process_due_service_request_dispatches()

        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        self.assertEqual(dispatch.attempts_count, 1)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_SENT)
        self.assertIsNotNone(dispatch.sent_at)
        self.assertEqual(dispatch.provider_message_id, 'wamid.test')
        log = ServiceWhatsAppMessageLog.objects.get(request=req, seller=seller)
        self.assertEqual(log.status, 'sent')
        self.assertEqual(log.meta_message_id, 'wamid.test')
        self.assertEqual(log.error_text, '')
        self.assertEqual(log.message_type, 'seller_request')
        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(
            ServiceMatch.objects.get(request=req, seller=seller).status,
            'new',
        )

    @patch(SENDER)
    def test_second_run_does_not_send_again(self, mock_send):
        mock_send.return_value = _ok_result()
        req, seller = self._queued()
        process_due_service_request_dispatches()

        process_due_service_request_dispatches()

        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        self.assertEqual(dispatch.attempts_count, 1)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_SENT)
        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(
            ServiceWhatsAppMessageLog.objects.filter(request=req, seller=seller).count(),
            1,
        )

    @patch(SENDER)
    def test_failures_retry_then_become_terminal(self, mock_send):
        mock_send.return_value = _fail_result()
        req, seller = self._queued()

        process_due_service_request_dispatches()
        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        self.assertEqual(dispatch.attempts_count, 1)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_QUEUED)
        self.assertGreater(dispatch.next_attempt_at, timezone.now())
        self.assertEqual(
            ServiceWhatsAppMessageLog.objects.filter(
                request=req, seller=seller, status='failed',
            ).count(),
            1,
        )

        process_due_service_request_dispatches()
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.attempts_count, 1)
        self.assertEqual(mock_send.call_count, 1)

        before_second = timezone.now()
        dispatch.next_attempt_at = before_second - timedelta(seconds=1)
        dispatch.save(update_fields=['next_attempt_at'])
        process_due_service_request_dispatches()
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.attempts_count, 2)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_QUEUED)
        delay = (dispatch.next_attempt_at - before_second).total_seconds()
        self.assertGreater(delay, 290)
        self.assertLess(delay, 320)

        dispatch.next_attempt_at = timezone.now() - timedelta(seconds=1)
        dispatch.save(update_fields=['next_attempt_at'])
        process_due_service_request_dispatches()
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.attempts_count, 3)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_FAILED)

        process_due_service_request_dispatches()
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.attempts_count, 3)
        self.assertEqual(mock_send.call_count, 3)

    @patch(SENDER)
    def test_second_attempt_success(self, mock_send):
        mock_send.side_effect = [_fail_result(), _ok_result('wamid.retry')]
        req, seller = self._queued()
        process_due_service_request_dispatches()
        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        dispatch.next_attempt_at = timezone.now() - timedelta(seconds=1)
        dispatch.save(update_fields=['next_attempt_at'])

        process_due_service_request_dispatches()

        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_SENT)
        self.assertEqual(dispatch.attempts_count, 2)
        self.assertEqual(dispatch.provider_message_id, 'wamid.retry')
        self.assertEqual(
            ServiceWhatsAppMessageLog.objects.filter(
                request=req, seller=seller, status='failed',
            ).count(),
            1,
        )
        self.assertEqual(
            ServiceWhatsAppMessageLog.objects.filter(
                request=req, seller=seller, status='sent',
            ).count(),
            1,
        )

    @patch(SENDER)
    def test_stale_processing_is_recovered_and_sent(self, mock_send):
        mock_send.return_value = _ok_result('wamid.recovered')
        req, seller = self._queued()
        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        dispatch.status = ServiceRequestDispatch.STATUS_PROCESSING
        dispatch.attempts_count = 1
        dispatch.last_attempt_at = timezone.now() - timedelta(minutes=6)
        dispatch.next_attempt_at = timezone.now() + timedelta(hours=1)
        dispatch.save()

        process_due_service_request_dispatches()

        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_SENT)
        self.assertEqual(dispatch.provider_message_id, 'wamid.recovered')
        self.assertEqual(mock_send.call_count, 1)

    @patch(SENDER)
    def test_stale_third_attempt_without_success_log_is_terminal(self, mock_send):
        req, seller = self._queued()
        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        dispatch.status = ServiceRequestDispatch.STATUS_PROCESSING
        dispatch.attempts_count = 3
        dispatch.last_attempt_at = timezone.now() - timedelta(minutes=6)
        dispatch.save()

        process_due_service_request_dispatches()

        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_FAILED)
        self.assertEqual(dispatch.attempts_count, 3)
        self.assertEqual(mock_send.call_count, 0)

        process_due_service_request_dispatches()

        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_FAILED)
        self.assertEqual(dispatch.attempts_count, 3)
        self.assertEqual(mock_send.call_count, 0)

    @patch(SENDER)
    def test_stale_third_attempt_with_success_log_is_reconciled(self, mock_send):
        req, seller = self._queued()
        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        dispatch.status = ServiceRequestDispatch.STATUS_PROCESSING
        dispatch.attempts_count = 3
        dispatch.last_attempt_at = timezone.now() - timedelta(minutes=6)
        dispatch.save()
        ServiceWhatsAppMessageLog.objects.create(
            request=req,
            seller=seller,
            phone='77002000041',
            message_type='seller_request',
            status='sent',
            meta_message_id='wamid.third-success',
        )

        process_due_service_request_dispatches()

        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_SENT)
        self.assertEqual(dispatch.attempts_count, 3)
        self.assertEqual(dispatch.provider_message_id, 'wamid.third-success')
        self.assertEqual(mock_send.call_count, 0)

    @patch(SENDER)
    def test_stale_second_attempt_uses_exactly_the_third_send(self, mock_send):
        mock_send.return_value = _ok_result('wamid.third')
        req, seller = self._queued()
        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        dispatch.status = ServiceRequestDispatch.STATUS_PROCESSING
        dispatch.attempts_count = 2
        dispatch.last_attempt_at = timezone.now() - timedelta(minutes=6)
        dispatch.save()

        process_due_service_request_dispatches()

        dispatch.refresh_from_db()
        self.assertEqual(dispatch.attempts_count, 3)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_SENT)
        self.assertEqual(mock_send.call_count, 1)

    @patch(SENDER)
    def test_existing_success_log_reconciles_without_send(self, mock_send):
        req, seller = self._queued()
        ServiceWhatsAppMessageLog.objects.create(
            request=req,
            seller=seller,
            phone='77002000041',
            message_type='seller_request',
            status='sent',
            meta_message_id='wamid.existing',
        )

        process_due_service_request_dispatches()

        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_SENT)
        self.assertEqual(dispatch.provider_message_id, 'wamid.existing')
        self.assertEqual(dispatch.attempts_count, 0)
        mock_send.assert_not_called()
        self.assertEqual(
            ServiceWhatsAppMessageLog.objects.filter(request=req, seller=seller).count(),
            1,
        )

    @patch(SENDER)
    def test_failure_log_redacts_access_token(self, mock_send):
        mock_send.return_value = _fail_result('Authorization Bearer SECRETTOKEN failed')
        req, seller = self._queued()

        process_due_service_request_dispatches()

        log = ServiceWhatsAppMessageLog.objects.get(request=req, seller=seller)
        self.assertEqual(log.status, 'failed')
        self.assertNotIn('SECRETTOKEN', log.error_text)
        self.assertNotIn('SECRETTOKEN', log.response_json)
        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        self.assertNotIn('SECRETTOKEN', dispatch.last_error)


class RuntimePolicyTests(WhatsAppDispatchTestCase):
    def _queued_seller(self, **seller_overrides):
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service()
        seller = self.make_seller(
            'A', '77002000051', services=[service], **seller_overrides,
        )
        req = self.make_request([service])
        match_services(req)
        return req, seller

    def _assert_skipped(self, req, seller, reason, mock_send):
        process_due_service_request_dispatches()
        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_SKIPPED)
        self.assertEqual(dispatch.skip_reason, reason)
        mock_send.assert_not_called()

    @patch(SENDER)
    def test_off_mode_skips(self, mock_send):
        req, seller = self._queued_seller()
        ServiceBroadcastSettings.objects.filter(pk=1).update(
            mode=ServiceBroadcastSettings.MODE_OFF,
        )
        self._assert_skipped(req, seller, SKIP_BROADCAST_DISABLED, mock_send)

    @patch(SENDER)
    def test_missing_settings_skips(self, mock_send):
        req, seller = self._queued_seller()
        ServiceBroadcastSettings.objects.all().delete()
        self._assert_skipped(req, seller, SKIP_BROADCAST_DISABLED, mock_send)

    @patch(SENDER)
    def test_unknown_mode_skips(self, mock_send):
        req, seller = self._queued_seller()
        ServiceBroadcastSettings.objects.filter(pk=1).update(mode='unexpected')
        self._assert_skipped(req, seller, SKIP_BROADCAST_DISABLED, mock_send)

    @patch(SENDER)
    def test_test_seller_lost_flag_is_skipped(self, mock_send):
        self.set_mode(ServiceBroadcastSettings.MODE_TEST)
        service = self.add_service('Ходовая')
        seller = self.make_seller(
            'A', '77002000061', services=[service], is_test_seller=True,
        )
        req = self.make_request([service])
        match_services(req)
        seller.is_test_seller = False
        seller.save(update_fields=['is_test_seller'])
        self._assert_skipped(req, seller, SKIP_NOT_TEST_SELLER, mock_send)

    @patch(SENDER)
    def test_paused_seller_is_skipped(self, mock_send):
        req, seller = self._queued_seller()
        seller.is_paused = True
        seller.save(update_fields=['is_paused'])
        self._assert_skipped(req, seller, SKIP_SELLER_PAUSED, mock_send)

    @patch(SENDER)
    def test_receive_requests_false_is_skipped(self, mock_send):
        req, seller = self._queued_seller()
        seller.receive_requests = False
        seller.save(update_fields=['receive_requests'])
        self._assert_skipped(req, seller, SKIP_RECEIVE_REQUESTS_DISABLED, mock_send)

    @patch(SENDER)
    def test_inactive_seller_is_skipped(self, mock_send):
        req, seller = self._queued_seller()
        seller.is_active = False
        seller.save(update_fields=['is_active'])
        self._assert_skipped(req, seller, SKIP_SELLER_INACTIVE, mock_send)

    @patch(SENDER)
    def test_live_still_sends_to_test_seller(self, mock_send):
        mock_send.return_value = _ok_result('wamid.live-test')
        req, seller = self._queued_seller(is_test_seller=True)

        process_due_service_request_dispatches()

        dispatch = ServiceRequestDispatch.objects.get(request=req, seller=seller)
        self.assertEqual(dispatch.status, ServiceRequestDispatch.STATUS_SENT)
        self.assertEqual(mock_send.call_count, 1)


class TemplateContractTests(WhatsAppDispatchTestCase):
    @patch.dict(os.environ, {
        'WHATSAPP_SERVICE_TEMPLATE_NAME': 'custom_service_template',
        'WHATSAPP_TEMPLATE_LANG': 'kk',
    })
    @patch(SENDER)
    def test_template_name_language_and_seven_parameters(self, mock_send):
        mock_send.return_value = _ok_result()
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        diagnostics = self.add_service('Диагностика')
        chassis = self.add_service('Ходовая')
        seller = self.make_seller('A', '77002000071', services=[diagnostics, chassis])
        req = self.make_request([diagnostics, chassis])
        match_services(req)

        process_due_service_request_dispatches()

        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs['template_name'], 'custom_service_template')
        self.assertEqual(kwargs['template_language'], 'kk')
        self.assertEqual(kwargs['to_phone'], seller.whatsapp)
        texts = [item['text'] for item in kwargs['body_parameters']]
        self.assertEqual(texts, [
            str(req.id),
            'Toyota',
            'Camry',
            'Диагностика, Ходовая',
            'Алматы',
            'Стук в ходовой',
            '77001234567',
        ])

    @patch(SENDER)
    def test_template_defaults_when_env_is_absent(self, mock_send):
        mock_send.return_value = _ok_result()
        self.set_mode(ServiceBroadcastSettings.MODE_LIVE)
        service = self.add_service()
        self.make_seller('A', '77002000081', services=[service])
        req = self.make_request([service])
        match_services(req)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('WHATSAPP_SERVICE_TEMPLATE_NAME', None)
            os.environ.pop('WHATSAPP_TEMPLATE_LANG', None)
            process_due_service_request_dispatches()

        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs['template_name'], 'zpt_request_notification')
        self.assertEqual(kwargs['template_language'], 'ru')
        self.assertEqual(len(kwargs['body_parameters']), 7)


class DispatchCommandTests(TestCase):
    @patch(
        'core.management.commands.dispatch_request_waves.'
        'process_due_service_request_dispatches'
    )
    @patch(
        'core.management.commands.dispatch_request_waves.process_due_dispatch_waves'
    )
    def test_command_runs_core_then_service_queue(self, core_processor, service_processor):
        order = []
        core_processor.side_effect = lambda **kwargs: order.append('core')
        service_processor.side_effect = lambda **kwargs: order.append('service')

        call_command('dispatch_request_waves')

        self.assertEqual(order, ['core', 'service'])
        core_processor.assert_called_once()
        service_processor.assert_called_once()

    @patch(
        'core.management.commands.dispatch_request_waves.'
        'process_due_service_request_dispatches'
    )
    @patch(
        'core.management.commands.dispatch_request_waves.process_due_dispatch_waves'
    )
    def test_core_failure_still_runs_service_queue(self, core_processor, service_processor):
        order = []

        def fail_core(**kwargs):
            order.append('core')
            raise RuntimeError('core boom')

        core_processor.side_effect = fail_core
        service_processor.side_effect = lambda **kwargs: order.append('service')
        stderr = StringIO()

        with self.assertRaises(CommandError) as caught:
            call_command('dispatch_request_waves', stderr=stderr)

        self.assertEqual(order, ['core', 'service'])
        self.assertIn('core RequestDispatch', str(caught.exception))
        self.assertIn('core RequestDispatch processor failed', stderr.getvalue())

    @patch(
        'core.management.commands.dispatch_request_waves.'
        'process_due_service_request_dispatches'
    )
    @patch(
        'core.management.commands.dispatch_request_waves.process_due_dispatch_waves'
    )
    def test_service_failure_still_runs_core_queue(self, core_processor, service_processor):
        order = []
        core_processor.side_effect = lambda **kwargs: order.append('core')

        def fail_service(**kwargs):
            order.append('service')
            raise RuntimeError('service boom')

        service_processor.side_effect = fail_service
        stderr = StringIO()

        with self.assertRaises(CommandError) as caught:
            call_command('dispatch_request_waves', stderr=stderr)

        self.assertEqual(order, ['core', 'service'])
        self.assertIn('service_requests ServiceRequestDispatch', str(caught.exception))
        self.assertIn(
            'service_requests ServiceRequestDispatch processor failed',
            stderr.getvalue(),
        )


class ServiceDispatchAdminTests(TestCase):
    def test_queue_admin_is_read_only_and_log_admin_stays_registered(self):
        self.assertIn(ServiceRequestDispatch, admin.site._registry)
        self.assertIn(ServiceWhatsAppMessageLog, admin.site._registry)
        model_admin = admin.site._registry[ServiceRequestDispatch]
        self.assertIsInstance(model_admin, ServiceRequestDispatchAdmin)
        User = get_user_model()
        user = User.objects.create_superuser(
            username='dispatch-admin',
            password='secret-pass',
            email='dispatch@example.com',
        )
        from django.test import RequestFactory
        request = RequestFactory().get('/admin/')
        request.user = user
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        self.assertTrue(model_admin.has_view_permission(request))
