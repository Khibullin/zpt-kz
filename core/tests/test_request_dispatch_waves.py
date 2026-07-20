from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from core.models import (
    BroadcastSettings,
    Match,
    Request,
    RequestDispatch,
    Seller,
    WhatsAppMessageLog,
)
from core.request_dispatch_service import (
    MAX_DISPATCH_SEND_ATTEMPTS,
    BUYER_WHATSAPP_LOG_SELLER_NAME,
    WaveRunLock,
    dispatch_failure_count,
    get_next_sendable_wave,
    process_due_dispatch_waves,
    record_dispatch_failure,
    resolve_whatsapp_status,
    send_single_dispatch,
    try_acquire_wave_lock,
    release_wave_lock,
)
from core.views import _build_dispatch_queue, _dispatch_to_json


def _ensure_broadcast_settings(**kwargs):
    settings, _ = BroadcastSettings.objects.get_or_create(pk=1)
    for key, value in kwargs.items():
        setattr(settings, key, value)
    settings.save()
    return settings


def _create_sellers(count: int, *, is_test: bool = False) -> list[Seller]:
    sellers = []
    for index in range(count):
        sellers.append(
            Seller.objects.create(
                name=f'Seller {index + 1}',
                whatsapp=f'7701000{index:04d}',
                transport_type='car',
                city='Алматы',
                receive_requests=True,
                is_test_seller=is_test,
            ),
        )
    return sellers


class RequestDispatchWaveDistributionTests(TestCase):
    def setUp(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )

    @patch('core.views.send_whatsapp_template')
    def test_forty_seven_sellers_split_into_five_waves(self, send_mock):
        send_mock.return_value = {'ok': True, 'message_id': 'wamid.test'}

        sellers = _create_sellers(47)
        dispatches = _build_dispatch_queue(self.request, sellers)

        self.assertEqual(len(dispatches), 47)
        wave_counts = {}
        for dispatch in dispatches:
            wave_counts[dispatch.wave_number] = wave_counts.get(dispatch.wave_number, 0) + 1

        self.assertEqual(wave_counts, {1: 10, 2: 10, 3: 10, 4: 10, 5: 7})
        self.assertEqual(send_mock.call_count, 10)

        wave_one = RequestDispatch.objects.filter(
            request=self.request,
            wave_number=1,
        )
        self.assertTrue(all(item.status == RequestDispatch.STATUS_SENT for item in wave_one))
        wave_two = RequestDispatch.objects.filter(
            request=self.request,
            wave_number=2,
        )
        self.assertTrue(all(item.status == RequestDispatch.STATUS_QUEUED for item in wave_two))


class CreateRequestDoesNotDrainGlobalQueueTests(TestCase):
    def setUp(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        self.client = Client()

    @patch('core.views._send_buyer_whatsapp_notification_async')
    @patch('core.views.send_whatsapp_template', return_value={'ok': True})
    @patch('core.views._find_matching_sellers')
    def test_create_request_does_not_send_old_queued_dispatches(
        self,
        matching_mock,
        send_mock,
        buyer_async_mock,
    ):
        old_request = Request.objects.create(
            transport_type='car',
            brand='Mercedes',
            model='E-class',
            category='Тормоза',
            city='Алматы',
            phone='77001112233',
        )
        old_seller = Seller.objects.create(
            name='Old seller',
            whatsapp='77002223344',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        old_dispatch = RequestDispatch.objects.create(
            request=old_request,
            seller=old_seller,
            wave_number=2,
            position_number=11,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now() - timedelta(days=3),
        )

        new_seller = Seller.objects.create(
            name='New seller',
            whatsapp='77003334455',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        matching_mock.return_value = ([new_seller], 'matched')

        response = self.client.post(
            '/api/create-request/',
            data={
                'transport_type': 'car',
                'brand': 'Toyota',
                'model': 'Windom',
                'category': 'Тормоза',
                'city': 'Алматы',
                'phone': '77476653398',
                'search_scope': 'city',
            },
        )
        self.assertEqual(response.status_code, 200)

        old_dispatch.refresh_from_db()
        self.assertEqual(old_dispatch.status, RequestDispatch.STATUS_QUEUED)
        self.assertEqual(
            send_mock.call_count,
            1,
            'Only the new request wave 1 should trigger WhatsApp send',
        )


class WorkerWavePacingTests(TestCase):
    def setUp(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )
        self.sellers = _create_sellers(30)
        base_time = timezone.now() - timedelta(hours=1)

        for index, seller in enumerate(self.sellers, start=1):
            wave_number = ((index - 1) // 10) + 1
            status = RequestDispatch.STATUS_SENT if wave_number == 1 else RequestDispatch.STATUS_QUEUED
            sent_at = base_time if wave_number == 1 else None
            RequestDispatch.objects.create(
                request=self.request,
                seller=seller,
                wave_number=wave_number,
                position_number=index,
                status=status,
                scheduled_at=base_time + timedelta(minutes=(wave_number - 1) * 5),
                sent_at=sent_at,
            )
            if wave_number == 1:
                Match.objects.create(
                    request=self.request,
                    seller=seller,
                    status='sent',
                    sent_at=sent_at,
                )

    @patch('core.request_dispatch_service.send_single_dispatch')
    def test_worker_sends_only_one_wave_per_request(self, send_mock):
        send_mock.return_value = {'ok': True, 'sent_at': timezone.now()}

        stats = process_due_dispatch_waves()
        self.assertEqual(stats['requests_processed'], 1)
        self.assertEqual(send_mock.call_count, 10)

        processed_waves = {
            call.args[0].wave_number for call in send_mock.call_args_list
        }
        self.assertEqual(processed_waves, {2})

    @patch('core.request_dispatch_service.send_single_dispatch')
    def test_worker_does_not_send_wave_three_immediately_after_wave_two(self, send_mock):
        wave_two_time = timezone.now() - timedelta(minutes=1)
        for dispatch in RequestDispatch.objects.filter(request=self.request, wave_number=2):
            dispatch.status = RequestDispatch.STATUS_SENT
            dispatch.sent_at = wave_two_time
            dispatch.save(update_fields=['status', 'sent_at'])

        send_mock.reset_mock()
        send_mock.return_value = {'ok': True, 'sent_at': timezone.now()}

        stats = process_due_dispatch_waves()
        self.assertEqual(stats['requests_processed'], 0)
        send_mock.assert_not_called()

    @patch('core.request_dispatch_service.send_single_dispatch')
    def test_worker_allows_wave_three_after_interval(self, send_mock):
        wave_two_time = timezone.now() - timedelta(minutes=6)
        for dispatch in RequestDispatch.objects.filter(request=self.request, wave_number=2):
            dispatch.status = RequestDispatch.STATUS_SENT
            dispatch.sent_at = wave_two_time
            dispatch.save(update_fields=['status', 'sent_at'])

        send_mock.return_value = {'ok': True, 'sent_at': timezone.now()}

        stats = process_due_dispatch_waves()
        self.assertEqual(stats['requests_processed'], 1)
        self.assertEqual(send_mock.call_count, 10)
        processed_waves = {
            call.args[0].wave_number for call in send_mock.call_args_list
        }
        self.assertEqual(processed_waves, {3})


class BroadcastSettingsWorkerGuardTests(TestCase):
    def setUp(self):
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )
        wave_one_seller = Seller.objects.create(
            name='Wave one seller',
            whatsapp='77004445566',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        wave_two_seller = Seller.objects.create(
            name='Wave two seller',
            whatsapp='77004445567',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        RequestDispatch.objects.create(
            request=self.request,
            seller=wave_one_seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=timezone.now() - timedelta(hours=1),
            sent_at=timezone.now() - timedelta(hours=1),
        )
        RequestDispatch.objects.create(
            request=self.request,
            seller=wave_two_seller,
            wave_number=2,
            position_number=11,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now() - timedelta(minutes=30),
        )

    @patch('core.request_dispatch_service.send_single_dispatch')
    def test_emergency_stop_blocks_worker(self, send_mock):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=True,
        )
        stats = process_due_dispatch_waves()
        self.assertEqual(stats['blocked'], 'emergency_stop')
        send_mock.assert_not_called()

    @patch('core.request_dispatch_service.send_single_dispatch')
    def test_off_mode_blocks_worker(self, send_mock):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_OFF,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        stats = process_due_dispatch_waves()
        self.assertEqual(stats['blocked'], 'mode_off')
        send_mock.assert_not_called()

    @patch('core.request_dispatch_service.send_single_dispatch')
    def test_test_mode_skips_non_test_sellers(self, send_mock):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_TEST,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        send_mock.return_value = {'ok': True, 'sent_at': timezone.now()}
        stats = process_due_dispatch_waves()
        self.assertEqual(stats['sent'], 0)
        send_mock.assert_not_called()


class WhatsAppStatusPresentationTests(TestCase):
    def setUp(self):
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )
        self.seller = Seller.objects.create(
            name='Status seller',
            whatsapp='77005556677',
            transport_type='car',
            city='Алматы',
        )

    def test_resolve_whatsapp_status_pending_for_queued_dispatch(self):
        dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=self.seller,
            wave_number=2,
            position_number=2,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now() + timedelta(minutes=5),
        )
        self.assertEqual(resolve_whatsapp_status(dispatch, None), 'pending')
        payload = _dispatch_to_json(dispatch, self.request)
        self.assertEqual(payload['whatsapp_status'], 'pending')

    def test_resolve_whatsapp_status_error_for_failed_match(self):
        dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=self.seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )
        match = Match.objects.create(
            request=self.request,
            seller=self.seller,
            status='error',
        )
        self.assertEqual(resolve_whatsapp_status(dispatch, match), 'error')

    def test_resolve_whatsapp_status_sent_dispatch_overrides_match_error(self):
        dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=self.seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=timezone.now(),
            sent_at=timezone.now(),
        )
        match = Match.objects.create(
            request=self.request,
            seller=self.seller,
            status='error',
        )
        self.assertEqual(resolve_whatsapp_status(dispatch, match), 'sent')

    def test_resolve_whatsapp_status_sent_when_success_log_exists(self):
        dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=self.seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_FAILED,
            scheduled_at=timezone.now(),
        )
        match = Match.objects.create(
            request=self.request,
            seller=self.seller,
            status='error',
        )
        WhatsAppMessageLog.objects.create(
            request_id=self.request.id,
            seller_name=self.seller.name,
            phone_clean='77005556677',
            is_success=True,
            status_text='sent',
            message_id='wamid.log-success',
        )
        self.assertEqual(resolve_whatsapp_status(dispatch, match), 'sent')


class SendSingleDispatchResultTests(TestCase):
    def setUp(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )
        self.seller = Seller.objects.create(
            name='Dispatch seller',
            whatsapp='77006667788',
            transport_type='car',
            city='Алматы',
        )
        self.dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=self.seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )

    @patch('core.views.send_whatsapp_template')
    def test_failed_meta_does_not_mark_dispatch_sent(self, send_mock):
        send_mock.return_value = {'ok': False, 'error': 'HTTP 400'}

        result = send_single_dispatch(self.dispatch)
        self.assertFalse(result['ok'])

        self.dispatch.refresh_from_db()
        self.assertEqual(self.dispatch.status, RequestDispatch.STATUS_QUEUED)
        self.assertIsNone(self.dispatch.sent_at)

    @patch('core.views.send_whatsapp_template')
    def test_sent_at_saved_individually_after_success(self, send_mock):
        timestamps = [
            timezone.now(),
            timezone.now() + timedelta(seconds=2),
        ]

        def _side_effect(*args, **kwargs):
            return {'ok': True, 'message_id': 'wamid.test'}

        send_mock.side_effect = _side_effect

        first_result = send_single_dispatch(self.dispatch)
        self.assertTrue(first_result['ok'])
        first_dispatch = RequestDispatch.objects.get(pk=self.dispatch.pk)
        first_sent_at = first_dispatch.sent_at

        seller_two = Seller.objects.create(
            name='Dispatch seller 2',
            whatsapp='77007778899',
            transport_type='car',
            city='Алматы',
        )
        dispatch_two = RequestDispatch.objects.create(
            request=self.request,
            seller=seller_two,
            wave_number=1,
            position_number=2,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )

        with patch('core.request_dispatch_service.timezone.now', return_value=timestamps[1]):
            second_result = send_single_dispatch(dispatch_two)

        self.assertTrue(second_result['ok'])
        dispatch_two.refresh_from_db()
        self.assertIsNotNone(dispatch_two.sent_at)
        self.assertIsNotNone(first_sent_at)


class ResultPageCopyTests(TestCase):
    @patch('core.views._send_buyer_whatsapp_notification_async')
    @patch('core.views.send_whatsapp_template', return_value={'ok': True})
    @patch('core.views._find_matching_sellers')
    def test_create_request_message_uses_queue_wording(
        self,
        matching_mock,
        send_mock,
        buyer_async_mock,
    ):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        sellers = _create_sellers(3)
        matching_mock.return_value = (sellers, 'matched')

        client = Client()
        response = client.post(
            '/api/create-request/',
            data={
                'transport_type': 'car',
                'brand': 'Toyota',
                'model': 'Windom',
                'category': 'Тормоза',
                'city': 'Алматы',
                'phone': '77476653398',
                'search_scope': 'city',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['matches'], 3)
        self.assertIn('очередь', payload['message'].lower())
        statuses = [
            item['whatsapp_status']
            for item in payload['seller_notifications']
            if item['whatsapp_status'] != 'sent'
        ]
        self.assertTrue(
            any(status == 'pending' for status in statuses) or len(sellers) <= 10,
        )


class WaveIntervalBoundaryTests(TestCase):
    def setUp(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )
        wave_one_time = timezone.now().replace(hour=10, minute=4, second=59, microsecond=0)
        seller_one = Seller.objects.create(
            name='Wave one',
            whatsapp='77001110001',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        seller_two = Seller.objects.create(
            name='Wave two',
            whatsapp='77001110002',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        RequestDispatch.objects.create(
            request=self.request,
            seller=seller_one,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=wave_one_time,
            sent_at=wave_one_time,
        )
        RequestDispatch.objects.create(
            request=self.request,
            seller=seller_two,
            wave_number=2,
            position_number=2,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=wave_one_time + timedelta(minutes=5),
        )
        self.settings = BroadcastSettings.load()
        self.too_early = wave_one_time + timedelta(minutes=4, seconds=1)
        self.ready_at = wave_one_time + timedelta(minutes=5)

    def test_wave_two_blocked_one_second_before_interval(self):
        self.assertIsNone(
            get_next_sendable_wave(
                self.request.id,
                settings=self.settings,
                now=self.too_early,
            ),
        )

    def test_wave_two_allowed_exactly_after_interval(self):
        self.assertEqual(
            get_next_sendable_wave(
                self.request.id,
                settings=self.settings,
                now=self.ready_at,
            ),
            2,
        )

    @patch('core.request_dispatch_service.send_single_dispatch')
    def test_two_worker_runs_do_not_send_multiple_waves(self, send_mock):
        send_mock.return_value = {'ok': True, 'sent_at': timezone.now()}

        with patch('core.request_dispatch_service.timezone.now', return_value=self.ready_at):
            first = process_due_dispatch_waves()
        self.assertEqual(first['requests_processed'], 1)
        self.assertEqual(send_mock.call_count, 1)

        for dispatch in RequestDispatch.objects.filter(request=self.request, wave_number=2):
            dispatch.status = RequestDispatch.STATUS_SENT
            dispatch.sent_at = self.ready_at
            dispatch.save(update_fields=['status', 'sent_at'])

        seller_three = Seller.objects.create(
            name='Wave three',
            whatsapp='77001110003',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        RequestDispatch.objects.create(
            request=self.request,
            seller=seller_three,
            wave_number=3,
            position_number=3,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=self.ready_at + timedelta(minutes=5),
        )

        send_mock.reset_mock()
        too_soon_for_wave_three = self.ready_at + timedelta(minutes=1)
        with patch('core.request_dispatch_service.timezone.now', return_value=too_soon_for_wave_three):
            second = process_due_dispatch_waves()
        self.assertEqual(second['requests_processed'], 0)
        send_mock.assert_not_called()


class PermanentFailureUnblocksWaveTests(TestCase):
    def setUp(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )
        self.settings = BroadcastSettings.load()
        wave_one_time = timezone.now() - timedelta(minutes=10)
        self.failed_seller = Seller.objects.create(
            name='Failed seller',
            whatsapp='77002220001',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        self.next_seller = Seller.objects.create(
            name='Next wave seller',
            whatsapp='77002220002',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        RequestDispatch.objects.create(
            request=self.request,
            seller=self.failed_seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_FAILED,
            scheduled_at=wave_one_time,
        )
        RequestDispatch.objects.create(
            request=self.request,
            seller=self.next_seller,
            wave_number=2,
            position_number=2,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=wave_one_time + timedelta(minutes=5),
        )
        Match.objects.create(
            request=self.request,
            seller=self.failed_seller,
            status='error',
        )

    def test_failed_dispatch_unblocks_next_wave(self):
        self.assertEqual(
            get_next_sendable_wave(
                self.request.id,
                settings=self.settings,
                now=timezone.now(),
            ),
            2,
        )

    def test_three_failures_mark_dispatch_failed(self):
        dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=Seller.objects.create(
                name='Retry seller',
                whatsapp='77002220003',
                transport_type='car',
                city='Алматы',
            ),
            wave_number=1,
            position_number=3,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )
        match = Match.objects.create(
            request=self.request,
            seller=dispatch.seller,
            status='prepared',
        )
        for _ in range(MAX_DISPATCH_SEND_ATTEMPTS):
            from core.models import WhatsAppMessageLog
            WhatsAppMessageLog.objects.create(
                request_id=self.request.id,
                seller_name=dispatch.seller.name,
                phone_clean='77002220003',
                is_success=False,
                status_text='http_error',
            )
        record_dispatch_failure(dispatch, match)
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_FAILED)
        self.assertEqual(dispatch_failure_count(dispatch), MAX_DISPATCH_SEND_ATTEMPTS)

    @patch('core.views.send_whatsapp_template', return_value={'ok': True, 'message_id': 'wamid.test'})
    def test_retry_success_updates_match_and_dispatch_to_sent(self, send_mock):
        dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=Seller.objects.create(
                name='Recover seller',
                whatsapp='77002220004',
                transport_type='car',
                city='Алматы',
            ),
            wave_number=1,
            position_number=4,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )
        match = Match.objects.create(
            request=self.request,
            seller=dispatch.seller,
            status='error',
        )

        result = send_single_dispatch(dispatch)
        self.assertTrue(result['ok'])

        dispatch.refresh_from_db()
        match.refresh_from_db()
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_SENT)
        self.assertEqual(match.status, 'sent')
        self.assertEqual(resolve_whatsapp_status(dispatch, match), 'sent')

    @patch('core.views.send_whatsapp_template')
    def test_success_syncs_dispatch_and_match_when_status_changed_during_meta_call(
        self,
        send_mock,
    ):
        dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=Seller.objects.create(
                name='Race seller',
                whatsapp='77002220005',
                transport_type='car',
                city='Алматы',
            ),
            wave_number=1,
            position_number=5,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )
        match = Match.objects.create(
            request=self.request,
            seller=dispatch.seller,
            status='prepared',
        )

        def _mark_failed_then_succeed(*args, **kwargs):
            dispatch.status = RequestDispatch.STATUS_FAILED
            dispatch.save(update_fields=['status'])
            match.status = 'error'
            match.save(update_fields=['status'])
            return {'ok': True, 'message_id': 'wamid.race-fix'}

        send_mock.side_effect = _mark_failed_then_succeed

        result = send_single_dispatch(dispatch)
        self.assertTrue(result['ok'])

        dispatch.refresh_from_db()
        match.refresh_from_db()
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_SENT)
        self.assertEqual(match.status, 'sent')
        self.assertEqual(resolve_whatsapp_status(dispatch, match), 'sent')


class WaveLockConcurrencyTests(TestCase):
    def test_second_worker_cannot_acquire_same_wave_lock(self):
        self.assertTrue(try_acquire_wave_lock(request_id=377, wave_number=2))
        try:
            self.assertFalse(try_acquire_wave_lock(request_id=377, wave_number=2))
        finally:
            release_wave_lock(request_id=377, wave_number=2)

    @patch('core.request_dispatch_service.send_single_dispatch')
    def test_parallel_worker_run_skips_locked_wave(self, send_mock):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )
        seller = Seller.objects.create(
            name='Locked wave seller',
            whatsapp='77003330001',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        RequestDispatch.objects.create(
            request=request,
            seller=seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )

        with WaveRunLock(request.id, 1) as held_lock:
            self.assertTrue(held_lock.acquired)
            stats = process_due_dispatch_waves()
            self.assertEqual(stats['skipped_locks'], 1)
            send_mock.assert_not_called()


class DispatchRetryIsolationTests(TestCase):
    """Failure counts are per request_id + phone + seller_name, not phone alone."""

    def setUp(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )
        self.shared_phone = '77009998877'

    def _create_dispatch(self, name: str) -> tuple[RequestDispatch, Match]:
        seller = Seller.objects.create(
            name=name,
            whatsapp=self.shared_phone,
            transport_type='car',
            city='Алматы',
        )
        dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )
        match = Match.objects.create(
            request=self.request,
            seller=seller,
            status='prepared',
        )
        return dispatch, match

    def test_same_phone_two_sellers_do_not_share_retry_count(self):
        dispatch_a, match_a = self._create_dispatch('Seller Alpha')
        dispatch_b, _ = self._create_dispatch('Seller Beta')

        for _ in range(MAX_DISPATCH_SEND_ATTEMPTS):
            WhatsAppMessageLog.objects.create(
                request_id=self.request.id,
                seller_name='Seller Alpha',
                phone_clean=self.shared_phone,
                is_success=False,
                status_text='http_error',
            )
        record_dispatch_failure(dispatch_a, match_a)
        dispatch_a.refresh_from_db()
        dispatch_b.refresh_from_db()

        self.assertEqual(dispatch_a.status, RequestDispatch.STATUS_FAILED)
        self.assertEqual(dispatch_b.status, RequestDispatch.STATUS_QUEUED)
        self.assertEqual(dispatch_failure_count(dispatch_b), 0)

    def test_buyer_log_does_not_affect_seller_retry(self):
        dispatch, match = self._create_dispatch('Seller Gamma')

        for _ in range(MAX_DISPATCH_SEND_ATTEMPTS):
            WhatsAppMessageLog.objects.create(
                request_id=self.request.id,
                seller_name=BUYER_WHATSAPP_LOG_SELLER_NAME,
                phone_clean=self.shared_phone,
                is_success=False,
                status_text='http_error',
            )

        self.assertEqual(dispatch_failure_count(dispatch), 0)
        record_dispatch_failure(dispatch, match)
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_QUEUED)

    @patch('core.views.send_whatsapp_template')
    def test_network_exception_counts_as_attempt(self, send_mock):
        send_mock.side_effect = ConnectionError('network down')
        dispatch, match = self._create_dispatch('Network seller')

        result = send_single_dispatch(dispatch)
        self.assertFalse(result['ok'])

        self.assertEqual(dispatch_failure_count(dispatch), 1)
        self.assertTrue(
            WhatsAppMessageLog.objects.filter(
                request_id=self.request.id,
                seller_name='Network seller',
                phone_clean=self.shared_phone,
                is_success=False,
                status_text='exception',
            ).exists(),
        )
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_QUEUED)
        match.refresh_from_db()
        self.assertEqual(match.status, 'error')

    @patch('core.views.send_whatsapp_template')
    def test_three_failures_mark_only_this_seller_dispatch_failed(self, send_mock):
        dispatch, match = self._create_dispatch('Retry target')

        def _meta_failure(phone, req, seller_name='', **kwargs):
            WhatsAppMessageLog.objects.create(
                request_id=req.id,
                seller_name=seller_name or '-',
                phone_clean=phone,
                is_success=False,
                status_text='http_error',
            )
            return {'ok': False, 'error': 'HTTP 400'}

        send_mock.side_effect = _meta_failure

        for _ in range(MAX_DISPATCH_SEND_ATTEMPTS - 1):
            send_single_dispatch(dispatch)
            dispatch.refresh_from_db()
            self.assertEqual(dispatch.status, RequestDispatch.STATUS_QUEUED)

        send_single_dispatch(dispatch)
        dispatch.refresh_from_db()
        match.refresh_from_db()

        self.assertEqual(dispatch.status, RequestDispatch.STATUS_FAILED)
        self.assertEqual(match.status, 'error')
        self.assertEqual(dispatch_failure_count(dispatch), MAX_DISPATCH_SEND_ATTEMPTS)

    @patch('core.views.send_whatsapp_template')
    def test_successful_retry_after_errors_becomes_sent(self, send_mock):
        dispatch, match = self._create_dispatch('Recover seller')
        send_mock.return_value = {'ok': False, 'error': 'HTTP 400'}

        for _ in range(2):
            WhatsAppMessageLog.objects.create(
                request_id=self.request.id,
                seller_name='Recover seller',
                phone_clean=self.shared_phone,
                is_success=False,
                status_text='http_error',
            )

        send_mock.return_value = {'ok': True, 'message_id': 'wamid.recover'}
        result = send_single_dispatch(dispatch)
        self.assertTrue(result['ok'])

        dispatch.refresh_from_db()
        match.refresh_from_db()
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_SENT)
        self.assertEqual(match.status, 'sent')
        self.assertIsNotNone(dispatch.sent_at)


class WaveLockReleaseTests(TestCase):
    def test_lock_reacquired_after_context_manager_exception(self):
        request_id = 501
        wave_number = 3

        class WaveProcessingError(RuntimeError):
            pass

        try:
            with WaveRunLock(request_id, wave_number) as wave_lock:
                self.assertTrue(wave_lock.acquired)
                raise WaveProcessingError('simulated Meta failure')
        except WaveProcessingError:
            pass

        self.assertTrue(try_acquire_wave_lock(request_id, wave_number))
        release_wave_lock(request_id, wave_number)

    @patch('core.views.send_whatsapp_template')
    def test_worker_releases_lock_after_dispatch_exception(self, send_mock):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Windom',
            category='Тормоза',
            city='Алматы',
            phone='77476653398',
        )
        seller = Seller.objects.create(
            name='Exception seller',
            whatsapp='77004440001',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        RequestDispatch.objects.create(
            request=request,
            seller=seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )
        send_mock.side_effect = RuntimeError('Meta timeout')

        stats = process_due_dispatch_waves()
        self.assertEqual(stats['errors'], 1)

        self.assertTrue(try_acquire_wave_lock(request.id, 1))
        release_wave_lock(request.id, 1)

    def test_different_request_wave_pairs_do_not_collide(self):
        self.assertTrue(try_acquire_wave_lock(request_id=100, wave_number=5))
        self.assertTrue(try_acquire_wave_lock(request_id=101, wave_number=4))
        try:
            self.assertFalse(try_acquire_wave_lock(request_id=100, wave_number=5))
        finally:
            release_wave_lock(request_id=100, wave_number=5)
            release_wave_lock(request_id=101, wave_number=4)
