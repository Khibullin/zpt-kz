from __future__ import annotations

from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.admin import SellerLeadPipelineRunAdmin
from core.models import SellerLead, SellerLeadPipelineRun
from core.services.seller_lead_pipeline_execution import execute_managed_seller_lead_pipeline
from core.services.seller_lead_pipeline_guard import (
    PipelineRunLock,
    SQLITE_PIPELINE_LOCK,
    release_pipeline_lock,
)
from core.services.seller_lead_search import build_search_queries
from core.services.seller_lead_search_rotation import (
    ROTATION_EPOCH,
    SEARCH_ROTATION_PROFILES,
    get_rotation_profile,
    resolve_pipeline_search,
)


def _discovery_query(search_term: str, category: str) -> str:
    return build_search_queries(
        city='Алматы',
        category=category,
        search_term=search_term,
    )[0][0]


def _mock_client(mapping: dict[str, list[dict]]):
    class FakeClient:
        def __init__(self):
            self.calls: list[str] = []

        def search(self, query, count=10):
            self.calls.append(query)
            return [
                {
                    'title': row.get('title', ''),
                    'url': row.get('url', ''),
                    'description': row.get('description', ''),
                }
                for row in mapping.get(query, [])
            ]

    return FakeClient()


def _profile_row(username: str) -> dict:
    return {
        'title': f'Shop {username}',
        'url': f'https://www.instagram.com/{username}/',
        'description': f'Profile {username}',
    }


@override_settings(
    SELLER_SEARCH_PROVIDER='brave',
    BRAVE_SEARCH_API_KEY='test-rotation-key',
    SELLER_SEARCH_ENABLED=True,
)
class SearchRotationProfileSelectionTests(SimpleTestCase):
    def test_july_15_selects_general_parts(self):
        profile, index = get_rotation_profile(date(2026, 7, 15))
        self.assertEqual(profile.slug, 'general_parts')
        self.assertEqual(index, 0)

    def test_july_16_selects_auto_dismantling(self):
        profile, index = get_rotation_profile(date(2026, 7, 16))
        self.assertEqual(profile.slug, 'auto_dismantling')
        self.assertEqual(index, 1)

    def test_july_17_selects_wholesale_parts(self):
        profile, index = get_rotation_profile(date(2026, 7, 17))
        self.assertEqual(profile.slug, 'wholesale_parts')
        self.assertEqual(index, 2)

    def test_cycle_repeats_after_14_days(self):
        profile_start, _ = get_rotation_profile(ROTATION_EPOCH)
        profile_repeat, index = get_rotation_profile(ROTATION_EPOCH + timedelta(days=14))
        self.assertEqual(profile_repeat.slug, profile_start.slug)
        self.assertEqual(index, 0)

    def test_same_day_returns_same_profile(self):
        first = get_rotation_profile(date(2026, 7, 20))
        second = get_rotation_profile(date(2026, 7, 20))
        self.assertEqual(first, second)

    def test_skipped_day_does_not_shift_sequence(self):
        profile_july_18, index = get_rotation_profile(date(2026, 7, 18))
        self.assertEqual(profile_july_18.slug, 'bmw_parts')
        self.assertEqual(index, 3)

    def test_bmw_profile_splits_search_term_and_category(self):
        resolved = resolve_pipeline_search(
            category='ignored',
            rotate_search_term=True,
            target_date=date(2026, 7, 18),
        )
        self.assertEqual(resolved.search_term, 'запчасти BMW')
        self.assertEqual(resolved.category, 'автозапчасти')

    def test_auto_dismantling_category_is_avtorazbor(self):
        resolved = resolve_pipeline_search(
            category='ignored',
            rotate_search_term=True,
            target_date=date(2026, 7, 16),
        )
        self.assertEqual(resolved.search_term, 'авторазбор')
        self.assertEqual(resolved.category, 'авторазбор')

    def test_explicit_category_does_not_override_rotation_profile(self):
        resolved = resolve_pipeline_search(
            category='ходовая часть',
            rotate_search_term=True,
            target_date=date(2026, 7, 16),
        )
        self.assertEqual(resolved.category, 'авторазбор')

    def test_rotate_and_search_term_together_rejected(self):
        with self.assertRaisesMessage(
            Exception,
            'Нельзя одновременно использовать --rotate-search-term и --search-term.',
        ):
            resolve_pipeline_search(
                category='автозапчасти',
                search_term='явный термин',
                rotate_search_term=True,
            )

    def test_search_term_without_rotation(self):
        resolved = resolve_pipeline_search(
            category='автозапчасти',
            search_term='автозапчасти оптом',
        )
        self.assertEqual(resolved.search_term, 'автозапчасти оптом')
        self.assertEqual(resolved.category, 'автозапчасти')
        self.assertFalse(resolved.rotation_enabled)

    def test_legacy_category_only_mode(self):
        resolved = resolve_pipeline_search(category='автозапчасти')
        self.assertEqual(resolved.search_term, 'автозапчасти')
        self.assertEqual(resolved.category, 'автозапчасти')

    def test_force_does_not_change_daily_profile(self):
        july_17 = date(2026, 7, 17)
        first = resolve_pipeline_search(
            category='автозапчасти',
            rotate_search_term=True,
            target_date=july_17,
        )
        second = resolve_pipeline_search(
            category='ходовая часть',
            rotate_search_term=True,
            target_date=july_17,
        )
        self.assertEqual(first.rotation_slug, second.rotation_slug)
        self.assertEqual(first.search_term, second.search_term)


class BuildSearchQueriesSeparationTests(SimpleTestCase):
    def test_search_term_used_in_query_category_stored_separately(self):
        query, city, stored_category = build_search_queries(
            city='Алматы',
            category='автозапчасти',
            search_term='запчасти BMW',
        )[0]
        self.assertEqual(query, 'site:instagram.com запчасти BMW Алматы WhatsApp')
        self.assertEqual(city, 'Алматы')
        self.assertEqual(stored_category, 'автозапчасти')

    def test_legacy_category_only_builds_same_query_as_before(self):
        queries = build_search_queries(city='Алматы', category='автозапчасти')
        self.assertEqual(
            queries[0][0],
            'site:instagram.com автозапчасти Алматы WhatsApp',
        )


@override_settings(
    SELLER_SEARCH_PROVIDER='brave',
    BRAVE_SEARCH_API_KEY='test-rotation-key',
    SELLER_SEARCH_ENABLED=True,
)
class SearchRotationPipelineIntegrationTests(TestCase):
    def setUp(self):
        if SQLITE_PIPELINE_LOCK.locked():
            release_pipeline_lock()

    def tearDown(self):
        if SQLITE_PIPELINE_LOCK.locked():
            release_pipeline_lock()

    def test_rotate_search_term_passes_term_to_discovery(self):
        target_date = date(2026, 7, 16)
        resolved = resolve_pipeline_search(
            category='автозапчасти',
            rotate_search_term=True,
            target_date=target_date,
        )
        query = _discovery_query(resolved.search_term, resolved.category)
        client = _mock_client({query: [_profile_row('rotation_shop_a')]})
        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category=resolved.category,
            search_term=resolved.search_term,
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=True,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_CRON,
            resolved_search=resolved,
            client=client,
        )
        self.assertEqual(result.stats.discovery.queries_executed, 1)
        self.assertEqual(client.calls, [query])

    def test_seller_lead_receives_rotation_category(self):
        target_date = date(2026, 7, 16)
        resolved = resolve_pipeline_search(
            category='автозапчасти',
            rotate_search_term=True,
            target_date=target_date,
        )
        query = _discovery_query(resolved.search_term, resolved.category)
        client = _mock_client({query: [_profile_row('rotation_cat_shop')]})
        execute_managed_seller_lead_pipeline(
            city='Алматы',
            category=resolved.category,
            search_term=resolved.search_term,
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=True,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            resolved_search=resolved,
            client=client,
        )
        lead = SellerLead.objects.get(instagram_username='rotation_cat_shop')
        self.assertEqual(lead.category, 'авторазбор')

    def test_journal_stores_rotation_metadata(self):
        target_date = date(2026, 7, 17)
        resolved = resolve_pipeline_search(
            category='автозапчасти',
            rotate_search_term=True,
            target_date=target_date,
        )
        query = _discovery_query(resolved.search_term, resolved.category)
        client = _mock_client({query: [_profile_row('journal_shop')]})
        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category=resolved.category,
            search_term=resolved.search_term,
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=True,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_CRON,
            resolved_search=resolved,
            client=client,
        )
        run = result.run
        self.assertEqual(run.search_term, 'автозапчасти оптом')
        self.assertTrue(run.rotation_enabled)
        self.assertEqual(run.rotation_slug, 'wholesale_parts')
        self.assertEqual(run.rotation_index, 2)

    def test_cooldown_skipped_run_stores_rotation_profile(self):
        target_date = date(2026, 7, 16)
        resolved = resolve_pipeline_search(
            category='автозапчасти',
            rotate_search_term=True,
            target_date=target_date,
        )
        SellerLeadPipelineRun.objects.create(
            trigger=SellerLeadPipelineRun.TRIGGER_CRON,
            status=SellerLeadPipelineRun.STATUS_SUCCESS,
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=2,
            started_at=timezone.now() - timedelta(minutes=5),
            finished_at=timezone.now() - timedelta(minutes=5),
        )
        client = _mock_client({})
        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category=resolved.category,
            search_term=resolved.search_term,
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=2,
            skip_discovery=False,
            skip_enrichment=True,
            cooldown_minutes=60,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_CRON,
            resolved_search=resolved,
            client=client,
        )
        self.assertTrue(result.cooldown_blocked)
        run = result.run
        self.assertEqual(run.status, SellerLeadPipelineRun.STATUS_SKIPPED)
        self.assertEqual(run.search_term, 'авторазбор')
        self.assertEqual(run.rotation_slug, 'auto_dismantling')
        self.assertFalse(client.calls)

    def test_dry_run_does_not_create_journal(self):
        before = SellerLeadPipelineRun.objects.count()
        out = StringIO()
        with patch(
            'core.services.seller_lead_search_rotation.get_rotation_profile',
            return_value=(SEARCH_ROTATION_PROFILES[1], 1),
        ), patch(
            'core.management.commands.run_seller_lead_pipeline.run_seller_lead_pipeline',
        ) as pipeline_mock:
            from core.services.seller_lead_pipeline import SellerLeadPipelineStats
            pipeline_mock.return_value = SellerLeadPipelineStats(dry_run=True)
            call_command(
                'run_seller_lead_pipeline',
                '--rotate-search-term',
                '--dry-run',
                '--skip-enrichment',
                stdout=out,
            )
        self.assertEqual(SellerLeadPipelineRun.objects.count(), before)

    def test_dry_run_stdout_shows_rotation_profile(self):
        out = StringIO()
        with patch(
            'core.services.seller_lead_search_rotation.get_rotation_profile',
            return_value=(SEARCH_ROTATION_PROFILES[1], 1),
        ), patch(
            'core.management.commands.run_seller_lead_pipeline.run_seller_lead_pipeline',
        ) as pipeline_mock:
            from core.services.seller_lead_pipeline import SellerLeadPipelineStats
            pipeline_mock.return_value = SellerLeadPipelineStats(dry_run=True)
            call_command(
                'run_seller_lead_pipeline',
                '--rotate-search-term',
                '--dry-run',
                '--skip-enrichment',
                stdout=out,
            )
        output = out.getvalue()
        self.assertIn('rotation: enabled', output)
        self.assertIn('rotation profile: auto_dismantling', output)
        self.assertIn('rotation position: 2/14', output)
        self.assertIn('search term: авторазбор', output)

    def test_lock_busy_skips_http(self):
        client = _mock_client({})
        with PipelineRunLock():
            result = execute_managed_seller_lead_pipeline(
                city='Алматы',
                category='автозапчасти',
                search_limit=10,
                lead_limit=2,
                max_queries_per_lead=1,
                skip_discovery=False,
                skip_enrichment=True,
                cooldown_minutes=0,
                force_run=False,
                trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
                client=client,
            )
        self.assertTrue(result.lock_busy)
        self.assertEqual(client.calls, [])

    def test_discovery_executes_single_brave_query(self):
        query = _discovery_query('автозапчасти', 'автозапчасти')
        client = _mock_client({
            query: [_profile_row(f'multi_{index}') for index in range(5)],
        })
        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=True,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        self.assertEqual(result.stats.discovery.queries_executed, 1)
        self.assertEqual(len(client.calls), 1)

    def test_lead_limit_respected(self):
        query = _discovery_query('автозапчасти', 'автозапчасти')
        client = _mock_client({
            query: [_profile_row(f'limit_{index}') for index in range(5)],
        })
        execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=True,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        self.assertEqual(SellerLead.objects.count(), 2)

    def test_existing_leads_not_reprocessed(self):
        SellerLead.objects.create(
            name='Old lead',
            instagram_username='old_rotation_shop',
            instagram_url='https://www.instagram.com/old_rotation_shop/',
            city='Алматы',
            category='автозапчасти',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )
        query = _discovery_query('автозапчасти', 'автозапчасти')
        client = _mock_client({query: [_profile_row('old_rotation_shop')]})
        result = execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=True,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        self.assertEqual(result.stats.discovery.new_profiles, 0)
        self.assertEqual(SellerLead.objects.count(), 1)

    def test_duplicates_not_created(self):
        query = _discovery_query('автозапчасти', 'автозапчасти')
        client = _mock_client({query: [_profile_row('dup_shop'), _profile_row('dup_shop')]})
        execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=True,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        self.assertEqual(SellerLead.objects.filter(instagram_username='dup_shop').count(), 1)

    def test_seller_lead_status_unchanged_on_rotation_run(self):
        query = _discovery_query('автозапчасти', 'автозапчасти')
        client = _mock_client({query: [_profile_row('status_keep_shop')]})
        execute_managed_seller_lead_pipeline(
            city='Алматы',
            category='автозапчасти',
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=1,
            skip_discovery=False,
            skip_enrichment=True,
            cooldown_minutes=0,
            force_run=False,
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            client=client,
        )
        lead = SellerLead.objects.get(instagram_username='status_keep_shop')
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)

    def test_command_rejects_rotate_with_search_term_before_http(self):
        with patch('core.services.seller_lead_pipeline.run_seller_lead_pipeline') as pipeline_mock:
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    'run_seller_lead_pipeline',
                    '--rotate-search-term',
                    '--search-term',
                    'явный термин',
                    '--dry-run',
                )
            pipeline_mock.assert_not_called()
        self.assertIn('--rotate-search-term', str(ctx.exception))

    def test_command_stdout_does_not_leak_api_key(self):
        out = StringIO()
        with patch(
            'core.management.commands.run_seller_lead_pipeline.run_seller_lead_pipeline',
        ) as pipeline_mock:
            from core.services.seller_lead_pipeline import SellerLeadPipelineStats
            pipeline_mock.return_value = SellerLeadPipelineStats(dry_run=True)
            call_command(
                'run_seller_lead_pipeline',
                '--dry-run',
                '--skip-enrichment',
                stdout=out,
            )
        output = out.getvalue()
        self.assertNotIn('test-rotation-key', output)
        self.assertNotIn('BRAVE_SEARCH_API_KEY', output)

    def test_journal_does_not_store_phone_numbers(self):
        query = _discovery_query('автозапчасти', 'автозапчасти')
        client = _mock_client({
            query: [_profile_row('phone_journal_shop')],
            'site:instagram.com/phone_journal_shop WhatsApp': [
                {
                    'title': 'WA',
                    'url': 'https://wa.me/77019998877',
                    'description': 'WhatsApp',
                },
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
        serialized = str(result.run.__dict__)
        self.assertNotIn('77019998877', serialized)


@override_settings(
    SELLER_SEARCH_PROVIDER='brave',
    BRAVE_SEARCH_API_KEY='test-rotation-key',
    SELLER_SEARCH_ENABLED=True,
)
class SearchRotationAdminTests(TestCase):
    def test_admin_list_display_includes_rotation_fields(self):
        admin = SellerLeadPipelineRunAdmin(SellerLeadPipelineRun, AdminSite())
        self.assertIn('search_term', admin.list_display)
        self.assertIn('rotation_enabled', admin.list_filter)

    def test_admin_readonly_includes_rotation_fields(self):
        admin = SellerLeadPipelineRunAdmin(SellerLeadPipelineRun, AdminSite())
        self.assertIn('search_term', admin.readonly_fields)
        self.assertIn('rotation_slug', admin.readonly_fields)

    def test_admin_disallows_mutations(self):
        admin = SellerLeadPipelineRunAdmin(SellerLeadPipelineRun, AdminSite())
        request = RequestFactory().get('/admin/')
        request.user = get_user_model().objects.create_superuser(
            username='rotation_admin',
            email='rotation@test.local',
            password='test-pass',
        )
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request))
        self.assertFalse(admin.has_delete_permission(request))

    def test_admin_shows_rotation_values_on_run(self):
        run = SellerLeadPipelineRun.objects.create(
            trigger=SellerLeadPipelineRun.TRIGGER_CRON,
            status=SellerLeadPipelineRun.STATUS_SUCCESS,
            city='Алматы',
            category='авторазбор',
            search_term='авторазбор',
            rotation_enabled=True,
            rotation_slug='auto_dismantling',
            rotation_index=1,
            search_limit=10,
            lead_limit=2,
            max_queries_per_lead=2,
        )
        admin = SellerLeadPipelineRunAdmin(SellerLeadPipelineRun, AdminSite())
        self.assertEqual(admin.discovery_new_profiles(run), 0)
        self.assertEqual(run.search_term, 'авторазбор')
        self.assertEqual(run.rotation_slug, 'auto_dismantling')
