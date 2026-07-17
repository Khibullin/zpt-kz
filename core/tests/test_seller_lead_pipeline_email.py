from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch
from django.utils import timezone

from core.models import SellerLead, SellerLeadContactCandidate, SellerLeadPipelineRun
from core.services.seller_lead_pipeline import (
    PipelineDiscoveryStats,
    PipelineEnrichmentStats,
    SellerLeadPipelineStats,
)
from core.services.seller_lead_pipeline_email import (
    build_pipeline_email_body,
    build_pipeline_email_subject,
    get_pipeline_notification_email,
    notify_pipeline_run_safely,
    send_pipeline_run_notification,
    should_send_pipeline_email,
)
from core.services.seller_lead_pipeline_execution import execute_managed_seller_lead_pipeline
from core.services.seller_lead_search_rotation import ResolvedPipelineSearch


def _resolved_search(**kwargs) -> ResolvedPipelineSearch:
    defaults = {
        'search_term': 'автозапчасти оптом',
        'category': 'автозапчасти',
        'rotation_enabled': True,
        'rotation_slug': 'wholesale_parts',
        'rotation_index': 2,
    }
    defaults.update(kwargs)
    return ResolvedPipelineSearch(**defaults)


def _discovery_stats(**kwargs) -> dict:
    defaults = {
        'queries_executed': 1,
        'results_count': 10,
        'recognized_profiles': 4,
        'new_profiles': 2,
        'skipped_duplicates': 2,
        'rejected_links': 1,
        'errors': 0,
    }
    defaults.update(kwargs)
    return defaults


def _enrichment_stats(**kwargs) -> dict:
    defaults = {
        'processed_leads': 2,
        'queries_executed': 4,
        'found_numbers': 0,
        'saved_primary_whatsapp': 0,
        'conflicts': 0,
        'without_contact': 2,
        'errors': 0,
    }
    defaults.update(kwargs)
    return defaults


def _create_run(**kwargs) -> SellerLeadPipelineRun:
    now = timezone.now()
    defaults = {
        'trigger': SellerLeadPipelineRun.TRIGGER_CRON,
        'status': SellerLeadPipelineRun.STATUS_SUCCESS,
        'is_dry_run': False,
        'city': 'Алматы',
        'category': 'автозапчасти',
        'search_limit': 10,
        'lead_limit': 3,
        'max_queries_per_lead': 3,
        'search_term': 'автозапчасти оптом',
        'rotation_enabled': True,
        'rotation_slug': 'wholesale_parts',
        'rotation_index': 2,
        'started_at': now - timedelta(seconds=3),
        'finished_at': now,
        'discovery_stats': _discovery_stats(),
        'enrichment_stats': _enrichment_stats(),
        'created_lead_ids': [],
    }
    defaults.update(kwargs)
    return SellerLeadPipelineRun.objects.create(**defaults)


EMAIL_SETTINGS = {
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    'SELLER_PIPELINE_EMAIL_ENABLED': True,
    'SELLER_PIPELINE_NOTIFICATION_EMAIL': 'pipeline@test.local',
    'ORDER_ADMIN_EMAIL': 'orders-admin@test.local',
    'EMAIL_HOST_USER': 'smtp-user@test.local',
    'PUBLIC_BASE_URL': 'https://zpt.kz',
}


@override_settings(**EMAIL_SETTINGS)
class SellerLeadPipelineEmailTests(TestCase):
    def setUp(self):
        mail.outbox.clear()

    def _send(self, run: SellerLeadPipelineRun) -> bool:
        return notify_pipeline_run_safely(run)

    def test_cron_success_sends_one_email(self):
        run = _create_run(status=SellerLeadPipelineRun.STATUS_SUCCESS)
        self.assertTrue(self._send(run))
        self.assertEqual(len(mail.outbox), 1)

    def test_cron_partial_sends_one_email(self):
        run = _create_run(
            status=SellerLeadPipelineRun.STATUS_PARTIAL,
            enrichment_stats=_enrichment_stats(errors=1),
        )
        self.assertTrue(self._send(run))
        self.assertEqual(len(mail.outbox), 1)

    def test_cron_failed_sends_one_email(self):
        run = _create_run(
            status=SellerLeadPipelineRun.STATUS_FAILED,
            error_message='Brave HTTP 500',
        )
        self.assertTrue(self._send(run))
        self.assertEqual(len(mail.outbox), 1)

    def test_cron_skipped_sends_one_email(self):
        run = _create_run(
            status=SellerLeadPipelineRun.STATUS_SKIPPED,
            skip_reason='Cooldown 60 мин. не истёк.',
        )
        self.assertTrue(self._send(run))
        self.assertEqual(len(mail.outbox), 1)

    def test_manual_success_does_not_send(self):
        run = _create_run(trigger=SellerLeadPipelineRun.TRIGGER_MANUAL)
        self.assertFalse(self._send(run))
        self.assertEqual(len(mail.outbox), 0)

    def test_dry_run_does_not_send(self):
        run = _create_run(is_dry_run=True)
        self.assertFalse(self._send(run))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(**{**EMAIL_SETTINGS, 'SELLER_PIPELINE_EMAIL_ENABLED': False})
    def test_disabled_setting_does_not_send(self):
        run = _create_run()
        self.assertFalse(self._send(run))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(**{
        **EMAIL_SETTINGS,
        'SELLER_PIPELINE_NOTIFICATION_EMAIL': '',
        'ORDER_ADMIN_EMAIL': '',
        'EMAIL_HOST_USER': '',
    })
    def test_empty_recipient_does_not_raise(self):
        run = _create_run()
        self.assertFalse(self._send(run))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(**EMAIL_SETTINGS)
    def test_uses_seller_pipeline_notification_email(self):
        self.assertEqual(get_pipeline_notification_email(), 'pipeline@test.local')
        run = _create_run()
        self._send(run)
        self.assertEqual(mail.outbox[0].to, ['pipeline@test.local'])

    @override_settings(**{
        **EMAIL_SETTINGS,
        'SELLER_PIPELINE_NOTIFICATION_EMAIL': '',
    })
    def test_fallback_to_order_admin_email(self):
        self.assertEqual(get_pipeline_notification_email(), 'orders-admin@test.local')
        run = _create_run()
        self._send(run)
        self.assertEqual(mail.outbox[0].to, ['orders-admin@test.local'])

    @override_settings(**{
        **EMAIL_SETTINGS,
        'SELLER_PIPELINE_NOTIFICATION_EMAIL': '',
        'ORDER_ADMIN_EMAIL': '',
    })
    def test_fallback_to_email_host_user(self):
        self.assertEqual(get_pipeline_notification_email(), 'smtp-user@test.local')
        run = _create_run()
        self._send(run)
        self.assertEqual(mail.outbox[0].to, ['smtp-user@test.local'])

    def test_subject_success_with_new_leads(self):
        run = _create_run(discovery_stats=_discovery_stats(new_profiles=2))
        subject = build_pipeline_email_subject(run)
        self.assertEqual(subject, 'ZPT.KZ: найдено 2 новых продавца — автозапчасти оптом')

    def test_subject_success_without_new_leads(self):
        run = _create_run(
            discovery_stats=_discovery_stats(new_profiles=0),
            search_term='запчасти BMW',
        )
        subject = build_pipeline_email_subject(run)
        self.assertEqual(subject, 'ZPT.KZ: новых продавцов нет — запчасти BMW')

    def test_subject_partial(self):
        run = _create_run(
            status=SellerLeadPipelineRun.STATUS_PARTIAL,
            search_term='запчасти Toyota',
        )
        subject = build_pipeline_email_subject(run)
        self.assertEqual(
            subject,
            'ZPT.KZ: pipeline завершён с предупреждениями — запчасти Toyota',
        )

    def test_subject_failed(self):
        run = _create_run(
            status=SellerLeadPipelineRun.STATUS_FAILED,
            search_term='грузовые автозапчасти',
        )
        subject = build_pipeline_email_subject(run)
        self.assertEqual(subject, 'ZPT.KZ: ошибка SellerLead pipeline — грузовые автозапчасти')

    def test_subject_skipped(self):
        run = _create_run(status=SellerLeadPipelineRun.STATUS_SKIPPED)
        subject = build_pipeline_email_subject(run)
        self.assertEqual(subject, 'ZPT.KZ: SellerLead pipeline пропущен — cooldown')

    def test_body_contains_run_uuid(self):
        run = _create_run()
        body = build_pipeline_email_body(run)
        self.assertIn(str(run.run_uuid), body)

    def test_body_contains_search_term(self):
        run = _create_run(search_term='автозапчасти оптом')
        body = build_pipeline_email_body(run)
        self.assertIn('автозапчасти оптом', body)

    def test_body_contains_rotation_slug_and_position(self):
        run = _create_run(rotation_slug='wholesale_parts', rotation_index=2)
        body = build_pipeline_email_body(run)
        self.assertIn('wholesale_parts', body)
        self.assertIn('3/14', body)

    def test_body_contains_discovery_stats(self):
        run = _create_run()
        body = build_pipeline_email_body(run)
        self.assertIn('DISCOVERY', body)
        self.assertIn('Результатов Brave: 10', body)
        self.assertIn('Новых профилей: 2', body)

    def test_body_contains_enrichment_stats(self):
        run = _create_run()
        body = build_pipeline_email_body(run)
        self.assertIn('ENRICHMENT', body)
        self.assertIn('Обработано лидов: 2', body)
        self.assertIn('Без контакта: 2', body)

    def test_body_contains_only_current_run_leads(self):
        included = SellerLead.objects.create(
            name='Included',
            instagram_username='optomkzru',
            city='Алматы',
            category='автозапчасти',
        )
        SellerLead.objects.create(
            name='Other',
            instagram_username='other.shop',
            city='Астана',
            category='автозапчасти',
        )
        run = _create_run(created_lead_ids=[included.pk])
        body = build_pipeline_email_body(run)
        self.assertIn('@optomkzru', body)
        self.assertNotIn('@other.shop', body)

    def test_body_contains_new_lead_username(self):
        lead = SellerLead.objects.create(
            name='Donix',
            instagram_username='donix.kz',
            city='Алматы',
            category='автозапчасти',
        )
        run = _create_run(created_lead_ids=[lead.pk])
        body = build_pipeline_email_body(run)
        self.assertIn('@donix.kz', body)

    def test_body_contains_saved_whatsapp(self):
        lead = SellerLead.objects.create(
            name='Shop',
            instagram_username='shop.kz',
            whatsapp='77001234567',
            whatsapp_confidence='high',
            city='Алматы',
            category='автозапчасти',
        )
        run = _create_run(created_lead_ids=[lead.pk])
        body = build_pipeline_email_body(run)
        self.assertIn('WhatsApp: 77001234567', body)
        self.assertIn('уверенность: high', body)

    def test_body_does_not_contain_source_text(self):
        lead = SellerLead.objects.create(
            name='Secret',
            instagram_username='secret.shop',
            whatsapp_source_text='SECRET_SOURCE_TEXT_SHOULD_NOT_APPEAR',
            city='Алматы',
            category='автозапчасти',
        )
        run = _create_run(created_lead_ids=[lead.pk])
        body = build_pipeline_email_body(run)
        self.assertNotIn('SECRET_SOURCE_TEXT_SHOULD_NOT_APPEAR', body)

    def test_body_does_not_contain_api_key(self):
        run = _create_run(
            status=SellerLeadPipelineRun.STATUS_FAILED,
            error_message='Auth failed for BSA-secret-key-0123456789',
        )
        body = build_pipeline_email_body(run)
        self.assertNotIn('BSA-secret-key-0123456789', body)
        self.assertIn('[REDACTED]', body)

    @override_settings(**{**EMAIL_SETTINGS, 'EMAIL_HOST_PASSWORD': 'super-secret-password'})
    def test_body_does_not_contain_email_host_password(self):
        run = _create_run(
            status=SellerLeadPipelineRun.STATUS_FAILED,
            error_message='SMTP auth failed: super-secret-password',
        )
        body = build_pipeline_email_body(run)
        self.assertNotIn('super-secret-password', body)

    @patch('core.services.seller_lead_pipeline_email.send_mail', side_effect=Exception('SMTP failed'))
    def test_smtp_exception_does_not_change_success_status(self, _mock_send):
        run = _create_run(status=SellerLeadPipelineRun.STATUS_SUCCESS)
        self.assertFalse(send_pipeline_run_notification(run))
        run.refresh_from_db()
        self.assertEqual(run.status, SellerLeadPipelineRun.STATUS_SUCCESS)

    @patch('core.services.seller_lead_pipeline_email.send_mail', side_effect=Exception('SMTP failed'))
    def test_smtp_exception_does_not_change_partial_status(self, _mock_send):
        run = _create_run(status=SellerLeadPipelineRun.STATUS_PARTIAL)
        self.assertFalse(send_pipeline_run_notification(run))
        run.refresh_from_db()
        self.assertEqual(run.status, SellerLeadPipelineRun.STATUS_PARTIAL)

    @patch('core.services.seller_lead_pipeline_email.send_mail', side_effect=Exception('SMTP failed'))
    def test_smtp_exception_preserves_failed_status(self, _mock_send):
        run = _create_run(status=SellerLeadPipelineRun.STATUS_FAILED, error_message='Already failed')
        self.assertFalse(send_pipeline_run_notification(run))
        run.refresh_from_db()
        self.assertEqual(run.status, SellerLeadPipelineRun.STATUS_FAILED)

    @patch('core.services.seller_lead_pipeline_execution.notify_pipeline_run_safely')
    @patch('core.services.seller_lead_pipeline_execution.run_seller_lead_pipeline')
    def test_execution_notifies_at_most_once_on_success(self, mock_run, mock_notify):
        stats = SellerLeadPipelineStats(
            discovery=PipelineDiscoveryStats(new_profiles=1),
            enrichment=PipelineEnrichmentStats(saved_primary=1),
            created_lead_ids=[1],
        )
        mock_run.return_value = stats
        with patch('core.services.seller_lead_pipeline_guard.try_acquire_pipeline_lock', return_value=True):
            with patch('core.services.seller_lead_pipeline_guard.release_pipeline_lock'):
                result = execute_managed_seller_lead_pipeline(
                    city='Алматы',
                    category='автозапчасти',
                    search_term='автозапчасти',
                    search_limit=10,
                    lead_limit=3,
                    max_queries_per_lead=3,
                    skip_discovery=False,
                    skip_enrichment=False,
                    cooldown_minutes=0,
                    force_run=True,
                    trigger=SellerLeadPipelineRun.TRIGGER_CRON,
                    resolved_search=_resolved_search(rotation_enabled=False),
                )
        self.assertIsNotNone(result.run)
        mock_notify.assert_called_once()

    @override_settings(**EMAIL_SETTINGS)
    @patch('core.services.seller_lead_pipeline_guard.try_acquire_pipeline_lock', return_value=False)
    def test_lock_busy_does_not_send_email(self, _mock_lock):
        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_term='автозапчасти',
            search_limit=10,
            lead_limit=3,
            max_queries_per_lead=3,
            skip_discovery=False,
            skip_enrichment=False,
            cooldown_minutes=0,
            force_run=True,
            trigger=SellerLeadPipelineRun.TRIGGER_CRON,
            resolved_search=_resolved_search(rotation_enabled=False),
        )
        self.assertTrue(result.lock_busy)
        self.assertIsNone(result.run)
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_url_uses_public_base_url(self):
        run = _create_run()
        body = build_pipeline_email_body(run)
        self.assertIn('https://zpt.kz/admin/core/sellerleadpipelinerun/', body)

    @patch('core.services.seller_lead_pipeline_email.reverse', side_effect=NoReverseMatch('missing'))
    def test_reverse_error_does_not_block_send(self, _mock_reverse):
        run = _create_run()
        self.assertTrue(send_pipeline_run_notification(run))
        self.assertEqual(len(mail.outbox), 1)

    @patch('core.services.seller_lead_pipeline_email.send_mail', side_effect=Exception('SMTP failed'))
    def test_seller_lead_status_unchanged_on_smtp_error(self, _mock_send):
        lead = SellerLead.objects.create(
            name='Stable',
            instagram_username='stable.shop',
            status=SellerLead.STATUS_NEEDS_REVIEW,
            city='Алматы',
            category='автозапчасти',
        )
        run = _create_run(
            status=SellerLeadPipelineRun.STATUS_SUCCESS,
            created_lead_ids=[lead.pk],
        )
        send_pipeline_run_notification(run)
        lead.refresh_from_db()
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)

    @patch('core.services.seller_lead_pipeline_email.send_mail', side_effect=Exception('SMTP failed'))
    def test_pipeline_data_not_rolled_back_on_smtp_error(self, _mock_send):
        run = _create_run(
            status=SellerLeadPipelineRun.STATUS_PARTIAL,
            discovery_stats=_discovery_stats(new_profiles=3),
            enrichment_stats=_enrichment_stats(errors=1),
        )
        send_pipeline_run_notification(run)
        run.refresh_from_db()
        self.assertEqual(run.status, SellerLeadPipelineRun.STATUS_PARTIAL)
        self.assertEqual(run.discovery_stats.get('new_profiles'), 3)
        self.assertEqual(run.enrichment_stats.get('errors'), 1)

    def test_should_send_requires_all_conditions(self):
        run = _create_run()
        self.assertTrue(should_send_pipeline_email(run))
        run.trigger = SellerLeadPipelineRun.TRIGGER_MANUAL
        self.assertFalse(should_send_pipeline_email(run))

    def test_conflict_candidates_count_in_body(self):
        lead = SellerLead.objects.create(
            name='Conflict',
            instagram_username='conflict.shop',
            city='Алматы',
            category='автозапчасти',
        )
        SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            value='77001112233',
            confidence='medium',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        run = _create_run(created_lead_ids=[lead.pk])
        body = build_pipeline_email_body(run)
        self.assertIn('конфликтов: 1', body)
