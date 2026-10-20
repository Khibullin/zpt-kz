from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import InstagramPublication, Request, SellerLead
from core.services.operations_email_notifications import (
    build_daily_operations_email,
    notify_new_request,
    send_daily_operations_email,
)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='zpt@test.local',
    EMAIL_HOST_USER='zpt@test.local',
    SELLER_PIPELINE_EMAIL_ENABLED=True,
    REQUEST_OWNER_EMAIL_ENABLED=True,
    REQUEST_OWNER_EMAIL='owner@test.local',
    SELLER_PIPELINE_NOTIFICATION_EMAIL='owner@test.local',
    PUBLIC_BASE_URL='https://zpt.kz',
)
class OperationsEmailNotificationTests(TestCase):
    def setUp(self):
        self.request = Request.objects.create(
            phone='+77001234567',
            vin='VIN-PRIVATE-123',
            brand='Toyota',
            model='Camry',
            category='Фильтр масляный',
            article='OIL-123',
            city='Алматы',
        )

    def test_new_request_email_contains_details_for_manual_processing(self):
        with patch('core.services.operations_email_notifications.send_mail') as send:
            self.assertTrue(notify_new_request(self.request.pk))

        self.assertEqual(send.call_count, 1)
        message = send.call_args.kwargs['message']
        self.assertIn(f'Заявка #{self.request.pk}', message)
        self.assertIn('Toyota Camry', message)
        self.assertIn('Алматы', message)
        self.assertIn('+77001234567', message)
        self.assertIn('VIN-PRIVATE-123', message)
        self.assertEqual(send.call_args.kwargs['recipient_list'], ['owner@test.local'])

    def test_daily_digest_includes_seller_request_and_instagram_statuses(self):
        SellerLead.objects.create(
            name='Parts Almaty',
            instagram_username='parts_almaty',
            instagram_url='https://instagram.com/parts_almaty',
            source_type='instagram',
            city='Алматы',
        )
        InstagramPublication.objects.create(
            request=self.request,
            placement=InstagramPublication.PLACEMENT_STORY,
            status=InstagramPublication.STATUS_PUBLISHED,
        )

        report = build_daily_operations_email(now=timezone.now())

        self.assertIn('ПОИСК ПРОДАВЦОВ', report)
        self.assertIn('Новых профилей: 1', report)
        self.assertIn('instagram: 1', report.lower())
        self.assertIn('ПУБЛИКАЦИИ В INSTAGRAM', report)
        self.assertIn('Опубликовано: 1', report)
        self.assertIn('ЗАЯВКИ ПОКУПАТЕЛЕЙ', report)
        self.assertIn('Toyota Camry', report)
        self.assertNotIn('+77001234567', report)
        self.assertNotIn('VIN-PRIVATE-123', report)

    def test_daily_digest_sends_to_configured_notification_recipient(self):
        with patch('core.services.operations_email_notifications.send_mail') as send:
            self.assertTrue(send_daily_operations_email())

        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.kwargs['recipient_list'], ['owner@test.local'])
        self.assertIn('утренняя сводка', send.call_args.kwargs['subject'].lower())

    @override_settings(REQUEST_OWNER_EMAIL_ENABLED=False)
    def test_request_email_can_be_disabled_separately(self):
        with patch('core.services.operations_email_notifications.send_mail') as send:
            self.assertFalse(notify_new_request(self.request.pk))
        send.assert_not_called()

    @override_settings(SELLER_PIPELINE_EMAIL_ENABLED=False)
    def test_manual_email_is_independent_of_pipeline_reports(self):
        with patch('core.services.operations_email_notifications.send_mail', return_value=1) as send:
            self.assertTrue(notify_new_request(self.request.pk))
        send.assert_called_once()

    def test_smtp_failure_does_not_break_request(self):
        with patch('core.services.operations_email_notifications.send_mail', side_effect=OSError('SMTP unavailable')):
            self.assertFalse(notify_new_request(self.request.pk))

    def test_service_email_includes_services_and_contact(self):
        from service_requests.models import ServiceRequest, Service
        req = ServiceRequest.objects.create(service_type='sto', city='Алматы', phone='77012345678', description='Проверить тормоза')
        req.services.add(Service.objects.create(name='Диагностика'))
        with patch('core.services.operations_email_notifications.send_mail', return_value=1) as send:
            self.assertTrue(notify_new_request(req.pk, service=True))
        message = send.call_args.kwargs['message']
        self.assertIn('Диагностика', message)
        self.assertIn('77012345678', message)
        self.assertIn('Проверить тормоза', message)
