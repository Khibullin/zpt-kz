from __future__ import annotations

import threading
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from core.admin import SellerLeadPipelineRunAdmin
from core.models import SellerLead, SellerLeadContactCandidate, SellerLeadPipelineRun
from core.services.seller_lead_pipeline import (
    PipelineDiscoveryStats,
    PipelineEnrichmentStats,
    SellerLeadPipelineStats,
)
from core.services.seller_lead_pipeline_execution import execute_managed_seller_lead_pipeline
from core.services.seller_lead_pipeline_guard import (
    DEFAULT_COOLDOWN_MINUTES,
    MAX_COOLDOWN_MINUTES,
    PipelineRunLock,
    SQLITE_PIPELINE_LOCK,
    check_pipeline_cooldown,
    release_pipeline_lock,
    try_acquire_pipeline_lock,
    validate_cooldown_minutes,
)
from core.services.seller_lead_pipeline_journal import (
    create_running_pipeline_run,
    discovery_stats_to_journal,
    enrichment_stats_to_journal,
    finalize_pipeline_run,
)


@override_settings(
    SELLER_SEARCH_PROVIDER='brave',
    BRAVE_SEARCH_API_KEY='test-key',
    SELLER_SEARCH_ENABLED=True,
)
class SellerLeadPipelineGuardTests(TestCase):
    DISCOVERY_QUERY = 'site:instagram.com автозапчасти Алматы WhatsApp'
    SECRET_KEY = 'BSA-valid-key-0123456789'

    def setUp(self):
        if SQLITE_PIPELINE_LOCK.locked():
            release_pipeline_lock()

    def tearDown(self):
        if SQLITE_PIPELINE_LOCK.locked():
            release_pipeline_lock()

    def _discovery_row(self, username: str):
        return {
            'title': f'Shop {username}',
            'url': f'https://www.instagram.com/{username}/',
            'description': f'Profile {username}',
        }

    def _mock_client(self, mapping):
        class FakeClient:
            def search(self, query, count=10):
                return [
                    {
                        'title': row.get('title', ''),
                        'url': row.get('url', ''),
                        'description': row.get('description', ''),
                    }
                    for row in mapping.get(query, [])
                ]

        return FakeClient()

    def _success_stats(self, *, created_ids: list[int] | None = None) -> SellerLeadPipelineStats:
        return SellerLeadPipelineStats(
            discovery=PipelineDiscoveryStats(
                queries_executed=1,
                results_found=2,
                profiles_parsed=1,
                new_profiles=1,
            ),
            enrichment=PipelineEnrichmentStats(
                leads_processed=1,
                queries_executed=1,
                candidates_found=1,
                high_confidence=1,
                saved_primary=1,
            ),
            created_lead_ids=created_ids or [101],
        )

    def _partial_stats(self) -> SellerLeadPipelineStats:
        stats = self._success_stats()
        stats.enrichment.errors = 1
        return stats

    def test_live_pipeline_creates_run_record(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('guard_new_shop')],
            'site:instagram.com/guard_new_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=False,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        self.assertIsNotNone(result.run)
        self.assertEqual(SellerLeadPipelineRun.objects.count(), 1)

    def test_running_status_at_start(self):
        run = create_running_pipeline_run(
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=False,
            cooldown_minutes=60,
            force_run=False,
        )
        self.assertEqual(run.status, SellerLeadPipelineRun.STATUS_RUNNING)
        self.assertIsNone(run.finished_at)

    def test_finished_at_and_stats_saved(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('guard_stats_shop')],
            'site:instagram.com/guard_stats_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=False,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        run = result.run
        self.assertEqual(run.status, SellerLeadPipelineRun.STATUS_SUCCESS)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.discovery_stats.get('new_profiles'), 1)
        self.assertEqual(run.enrichment_stats.get('saved_primary_whatsapp'), 1)
        self.assertTrue(run.created_lead_ids)

    def test_trigger_manual_and_cron_saved(self):
        for trigger in (
            SellerLeadPipelineRun.TRIGGER_MANUAL,
            SellerLeadPipelineRun.TRIGGER_CRON,
        ):
            SellerLeadPipelineRun.objects.all().delete()
            client = self._mock_client({
                self.DISCOVERY_QUERY: [self._discovery_row(f'guard_{trigger}_shop')],
                f'site:instagram.com/guard_{trigger}_shop WhatsApp': [
                    {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
                ],
            })
            result = execute_managed_seller_lead_pipeline(
                city='Алматы',
                category='автозапчасти',
                search_limit=10,
                lead_limit=1,
                max_queries_per_lead=1,
                skip_discovery=False,
                skip_enrichment=False,
                cooldown_minutes=0,
                force_run=False,
                trigger=trigger,
                client=client,
            )
            self.assertEqual(result.run.trigger, trigger)

    def test_cooldown_blocks_second_run(self):
        SellerLeadPipelineRun.objects.create(
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            status=SellerLeadPipelineRun.STATUS_SUCCESS,
            is_dry_run=False,
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        queries: list[str] = []

        class CountingClient:
            def search(self, query, count=10):
                queries.append(query)
                return []

        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=False,
            cooldown_minutes=60,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=CountingClient(),
        )
        self.assertTrue(result.cooldown_blocked)
        self.assertEqual(result.run.status, SellerLeadPipelineRun.STATUS_SKIPPED)
        self.assertEqual(queries, [])

    def test_force_bypasses_cooldown(self):
        SellerLeadPipelineRun.objects.create(
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            status=SellerLeadPipelineRun.STATUS_SUCCESS,
            is_dry_run=False,
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('guard_force_shop')],
            'site:instagram.com/guard_force_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=False,
            cooldown_minutes=60,
            force_run=True,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        self.assertFalse(result.cooldown_blocked)
        self.assertEqual(result.run.status, SellerLeadPipelineRun.STATUS_SUCCESS)

    def test_force_does_not_bypass_active_lock(self):
        self.assertTrue(try_acquire_pipeline_lock())
        try:
            result = execute_managed_seller_lead_pipeline(
                city='Алматы',
                category='автозапчасти',
                search_limit=10,
                lead_limit=1,
                max_queries_per_lead=1,
                skip_discovery=False,
                skip_enrichment=False,
                cooldown_minutes=0,
                force_run=True,
                trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
                client=self._mock_client({}),
            )
            self.assertTrue(result.lock_busy)
        finally:
            release_pipeline_lock()

    def test_concurrent_run_blocked_without_http(self):
        self.assertTrue(try_acquire_pipeline_lock())
        queries: list[str] = []

        class CountingClient:
            def search(self, query, count=10):
                queries.append(query)
                return []

        try:
            result = execute_managed_seller_lead_pipeline(
                city='Алматы',
                category='автозапчасти',
                search_limit=10,
                lead_limit=1,
                max_queries_per_lead=1,
                skip_discovery=False,
                skip_enrichment=False,
                cooldown_minutes=0,
                force_run=False,
                trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
                client=CountingClient(),
            )
            self.assertTrue(result.lock_busy)
            self.assertEqual(queries, [])
        finally:
            release_pipeline_lock()

    def test_lock_released_after_success(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('guard_unlock_shop')],
            'site:instagram.com/guard_unlock_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=False,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        self.assertFalse(SQLITE_PIPELINE_LOCK.locked())
        self.assertTrue(try_acquire_pipeline_lock())
        release_pipeline_lock()

    def test_lock_released_after_exception(self):
        with patch(
            'core.services.seller_lead_pipeline_execution.run_seller_lead_pipeline',
            side_effect=RuntimeError('pipeline failed'),
        ):
            with self.assertRaises(RuntimeError):
                execute_managed_seller_lead_pipeline(
                    city='Алматы',
                    category='автозапчасти',
                    search_limit=10,
                    lead_limit=1,
                    max_queries_per_lead=1,
                    skip_discovery=False,
                    skip_enrichment=False,
                    cooldown_minutes=0,
                    force_run=False,
                    trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
                    client=self._mock_client({}),
                )
        self.assertFalse(SQLITE_PIPELINE_LOCK.locked())
        run = SellerLeadPipelineRun.objects.get()
        self.assertEqual(run.status, SellerLeadPipelineRun.STATUS_FAILED)

    def test_failed_run_stores_safe_error_message(self):
        with patch(
            'core.services.seller_lead_pipeline_execution.run_seller_lead_pipeline',
            side_effect=RuntimeError(f'failure without {self.SECRET_KEY}'),
        ):
            with self.assertRaises(RuntimeError):
                execute_managed_seller_lead_pipeline(
                    city='Алматы',
                    category='автозапчасти',
                    search_limit=10,
                    lead_limit=1,
                    max_queries_per_lead=1,
                    skip_discovery=False,
                    skip_enrichment=False,
                    cooldown_minutes=0,
                    force_run=False,
                    trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
                    client=self._mock_client({}),
                )
        run = SellerLeadPipelineRun.objects.get()
        self.assertNotIn(self.SECRET_KEY, run.error_message)

    def test_partial_status_on_lead_errors(self):
        with patch(
            'core.services.seller_lead_pipeline_execution.run_seller_lead_pipeline',
            return_value=self._partial_stats(),
        ):
            result = execute_managed_seller_lead_pipeline(
                city='Алматы',
                category='автозапчасти',
                search_limit=10,
                lead_limit=1,
                max_queries_per_lead=1,
                skip_discovery=False,
                skip_enrichment=False,
                cooldown_minutes=0,
                force_run=False,
                trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
                client=self._mock_client({}),
            )
        self.assertEqual(result.run.status, SellerLeadPipelineRun.STATUS_PARTIAL)

    def test_dry_run_does_not_create_journal(self):
        before_runs = SellerLeadPipelineRun.objects.count()
        before_leads = SellerLead.objects.count()
        before_candidates = SellerLeadContactCandidate.objects.count()
        stdout = StringIO()
        with patch(
            'core.management.commands.run_seller_lead_pipeline.run_seller_lead_pipeline',
            return_value=self._success_stats(),
        ):
            call_command(
                'run_seller_lead_pipeline',
                lead_limit=1,
                max_queries_per_lead=1,
                dry_run=True,
                stdout=stdout,
            )
        self.assertEqual(SellerLeadPipelineRun.objects.count(), before_runs)
        self.assertEqual(SellerLead.objects.count(), before_leads)
        self.assertEqual(SellerLeadContactCandidate.objects.count(), before_candidates)
        self.assertIn('Dry-run: база данных не изменялась.', stdout.getvalue())

    def test_dry_run_not_blocked_by_cooldown(self):
        SellerLeadPipelineRun.objects.create(
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            status=SellerLeadPipelineRun.STATUS_SUCCESS,
            is_dry_run=False,
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        with patch(
            'core.management.commands.run_seller_lead_pipeline.run_seller_lead_pipeline',
            return_value=self._success_stats(),
        ) as mocked:
            call_command(
                'run_seller_lead_pipeline',
                cooldown_minutes=60,
                dry_run=True,
            )
            mocked.assert_called_once()

    def test_cooldown_zero_disables_check(self):
        SellerLeadPipelineRun.objects.create(
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            status=SellerLeadPipelineRun.STATUS_SUCCESS,
            is_dry_run=False,
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        result = check_pipeline_cooldown(cooldown_minutes=0, force_run=False)
        self.assertTrue(result.allowed)

    def test_cooldown_validation_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            validate_cooldown_minutes(-1)
        with self.assertRaises(ValueError):
            validate_cooldown_minutes(MAX_COOLDOWN_MINUTES + 1)
        with self.assertRaises(CommandError):
            call_command('run_seller_lead_pipeline', cooldown_minutes=-1, dry_run=True)
        with self.assertRaises(CommandError):
            call_command('run_seller_lead_pipeline', cooldown_minutes=MAX_COOLDOWN_MINUTES + 1, dry_run=True)

    def test_invalid_trigger_rejected(self):
        with self.assertRaises(CommandError):
            call_command('run_seller_lead_pipeline', trigger='invalid', dry_run=True)

    def test_journal_does_not_store_phones_or_source_text(self):
        enrichment = PipelineEnrichmentStats(
            leads_processed=1,
            candidates_found=1,
            high_confidence=1,
            saved_primary=1,
            lead_reports=[],
        )
        journal = enrichment_stats_to_journal(enrichment)
        self.assertNotIn('77011234567', str(journal))
        self.assertNotIn('source_text', journal)

        run = SellerLeadPipelineRun.objects.create(
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            status=SellerLeadPipelineRun.STATUS_RUNNING,
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
        )
        finalize_pipeline_run(
            run,
            stats=SellerLeadPipelineStats(
                enrichment=enrichment,
                discovery=PipelineDiscoveryStats(new_profiles=1),
                created_lead_ids=[1],
            ),
        )
        run.refresh_from_db()
        self.assertNotIn('77011234567', str(run.enrichment_stats))

    def test_admin_disallows_add_change_delete(self):
        admin = SellerLeadPipelineRunAdmin(SellerLeadPipelineRun, AdminSite())
        request = RequestFactory().get('/admin/')
        request.user = get_user_model().objects.create_superuser(
            username='pipeline_admin',
            email='admin@test.local',
            password='test-pass',
        )
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request))
        self.assertFalse(admin.has_delete_permission(request))

    def test_sqlite_fallback_lock_blocks_second_acquire(self):
        self.assertTrue(try_acquire_pipeline_lock())
        self.assertFalse(try_acquire_pipeline_lock())
        release_pipeline_lock()

    def test_pipeline_after_cooldown_expired_allowed(self):
        SellerLeadPipelineRun.objects.create(
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            status=SellerLeadPipelineRun.STATUS_SUCCESS,
            is_dry_run=False,
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            started_at=timezone.now() - timedelta(hours=2),
            finished_at=timezone.now() - timedelta(hours=2),
        )
        result = check_pipeline_cooldown(cooldown_minutes=60, force_run=False)
        self.assertTrue(result.allowed)

    def test_seller_lead_status_unchanged_on_live_run(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('guard_status_keep')],
            'site:instagram.com/guard_status_keep WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=False,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        lead = SellerLead.objects.get(instagram_username='guard_status_keep')
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)

    def test_dry_run_does_not_change_existing_whatsapp(self):
        lead = SellerLead.objects.create(
            name='Existing',
            instagram_username='guard_existing',
            instagram_url='https://www.instagram.com/guard_existing/',
            city='Алматы',
            category='автозапчасти',
            status=SellerLead.STATUS_NEEDS_REVIEW,
            whatsapp='77011234567',
            whatsapp_confidence='high',
            whatsapp_source_text='snippet',
        )
        with patch(
            'core.management.commands.run_seller_lead_pipeline.run_seller_lead_pipeline',
            return_value=self._success_stats(),
        ):
            call_command('run_seller_lead_pipeline', dry_run=True)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(lead.whatsapp_confidence, 'high')
        self.assertEqual(lead.whatsapp_source_text, 'snippet')

    def test_journal_discovery_mapping(self):
        journal = discovery_stats_to_journal(
            PipelineDiscoveryStats(
                queries_executed=1,
                results_found=5,
                profiles_parsed=4,
                new_profiles=2,
                duplicates_skipped=1,
                links_rejected=1,
                errors=0,
            ),
        )
        self.assertEqual(journal['results_count'], 5)
        self.assertEqual(journal['recognized_profiles'], 4)
