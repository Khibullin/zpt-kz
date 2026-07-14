from __future__ import annotations

import io
import json
import logging
from email.message import Message
from io import StringIO
from unittest.mock import patch
from urllib import error

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import SellerLead, SellerLeadContactCandidate
from core.services.seller_lead_pipeline import (
    MAX_LEAD_LIMIT,
    MAX_QUERIES_PER_LEAD_LIMIT,
    MAX_SEARCH_LIMIT,
    SellerLeadPipelineConfigError,
    run_seller_lead_pipeline,
    validate_pipeline_limits,
)
from core.services.seller_lead_search import (
    BraveSearchClient,
    SellerLeadSearchHTTPError,
    SellerLeadSearchTimeoutError,
)


class PipelineLimitValidationTests(TestCase):
    def test_zero_search_limit_rejected(self):
        with self.assertRaises(SellerLeadPipelineConfigError):
            validate_pipeline_limits(search_limit=0, lead_limit=3, max_queries_per_lead=3)

    def test_zero_lead_limit_rejected(self):
        with self.assertRaises(SellerLeadPipelineConfigError):
            validate_pipeline_limits(search_limit=10, lead_limit=0, max_queries_per_lead=3)

    def test_excessive_search_limit_rejected(self):
        with self.assertRaises(SellerLeadPipelineConfigError):
            validate_pipeline_limits(
                search_limit=MAX_SEARCH_LIMIT + 1,
                lead_limit=3,
                max_queries_per_lead=3,
            )

    def test_excessive_lead_limit_rejected(self):
        with self.assertRaises(SellerLeadPipelineConfigError):
            validate_pipeline_limits(
                search_limit=10,
                lead_limit=MAX_LEAD_LIMIT + 1,
                max_queries_per_lead=3,
            )

    def test_excessive_max_queries_rejected(self):
        with self.assertRaises(SellerLeadPipelineConfigError):
            validate_pipeline_limits(
                search_limit=10,
                lead_limit=3,
                max_queries_per_lead=MAX_QUERIES_PER_LEAD_LIMIT + 1,
            )


@override_settings(
    SELLER_SEARCH_PROVIDER='brave',
    BRAVE_SEARCH_API_KEY='test-key',
    SELLER_SEARCH_ENABLED=True,
)
class SellerLeadPipelineTests(TestCase):
    DISCOVERY_QUERY = 'site:instagram.com автозапчасти Алматы WhatsApp'

    def _discovery_row(self, username: str):
        return {
            'title': f'Shop {username}',
            'url': f'https://www.instagram.com/{username}/',
            'description': f'Profile {username}',
        }

    def _mock_client(self, mapping: dict[str, list[dict]]):
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

    def _existing_lead(self, username: str, *, whatsapp: str = ''):
        return SellerLead.objects.create(
            name=f'Existing {username}',
            instagram_username=username,
            instagram_url=f'https://www.instagram.com/{username}/',
            city='Алматы',
            category='автозапчасти',
            source_type='web_search',
            status=SellerLead.STATUS_NEEDS_REVIEW,
            whatsapp=whatsapp,
        )

    def test_full_pipeline_creates_new_seller_leads(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_new_shop')],
            'site:instagram.com/pipeline_new_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        stats = run_seller_lead_pipeline(
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        lead = SellerLead.objects.get(instagram_username='pipeline_new_shop')
        self.assertEqual(stats.discovery.new_profiles, 1)
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(stats.enrichment.saved_primary, 1)

    def test_enrichment_only_processes_leads_from_current_run(self):
        self._existing_lead('pipeline_old_shop')
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_fresh_shop')],
            'site:instagram.com/pipeline_fresh_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
            'site:instagram.com/pipeline_old_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77019876543', 'description': 'WhatsApp'},
            ],
        })
        run_seller_lead_pipeline(
            search_limit=10,
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        old = SellerLead.objects.get(instagram_username='pipeline_old_shop')
        fresh = SellerLead.objects.get(instagram_username='pipeline_fresh_shop')
        self.assertEqual(old.whatsapp, '')
        self.assertEqual(fresh.whatsapp, '77011234567')

    def test_existing_whatsapp_lead_not_reprocessed(self):
        self._existing_lead('pipeline_saved_shop', whatsapp='77011234567')
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_saved_shop')],
        })
        stats = run_seller_lead_pipeline(
            search_limit=10,
            lead_limit=3,
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        self.assertEqual(stats.discovery.new_profiles, 0)
        self.assertEqual(stats.enrichment.leads_processed, 0)

    def test_single_high_contact_saved(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_high_shop')],
            'site:instagram.com/pipeline_high_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp Business'},
            ],
        })
        run_seller_lead_pipeline(
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        lead = SellerLead.objects.get(instagram_username='pipeline_high_shop')
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(lead.whatsapp_confidence, 'high')

    def test_single_medium_contact_saved(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_medium_shop')],
            'site:instagram.com/pipeline_medium_shop WhatsApp': [
                {
                    'title': 'pipeline_medium_shop shop',
                    'url': 'https://www.instagram.com/pipeline_medium_shop/',
                    'description': 'pipeline_medium_shop +7 701 123 45 67',
                },
            ],
        })
        run_seller_lead_pipeline(
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        lead = SellerLead.objects.get(instagram_username='pipeline_medium_shop')
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(lead.whatsapp_confidence, 'medium')

    def test_conflict_creates_candidates_and_keeps_whatsapp_empty(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_conflict_shop')],
            'site:instagram.com/pipeline_conflict_shop WhatsApp': [
                {'title': 'A', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
                {'title': 'B', 'url': 'https://wa.me/77019876543', 'description': 'WhatsApp'},
            ],
        })
        stats = run_seller_lead_pipeline(
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=False,
            client=client,
        )
        lead = SellerLead.objects.get(instagram_username='pipeline_conflict_shop')
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.contact_candidates.count(), 2)
        self.assertEqual(stats.enrichment.conflicts, 1)

    def test_repeat_pipeline_does_not_create_duplicates(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_dup_shop')],
            'site:instagram.com/pipeline_dup_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        run_seller_lead_pipeline(lead_limit=1, max_queries_per_lead=1, dry_run=False, client=client)
        run_seller_lead_pipeline(lead_limit=1, max_queries_per_lead=1, dry_run=False, client=client)
        self.assertEqual(SellerLead.objects.filter(instagram_username='pipeline_dup_shop').count(), 1)

    def test_dry_run_does_not_create_seller_leads(self):
        before = SellerLead.objects.count()
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_dry_shop')],
            'site:instagram.com/pipeline_dry_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        stats = run_seller_lead_pipeline(
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        self.assertEqual(SellerLead.objects.count(), before)
        self.assertEqual(stats.discovery.new_profiles, 1)
        self.assertTrue(stats.dry_run)

    def test_dry_run_does_not_create_candidates(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_dry_conflict')],
            'site:instagram.com/pipeline_dry_conflict WhatsApp': [
                {'title': 'A', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
                {'title': 'B', 'url': 'https://wa.me/77019876543', 'description': 'WhatsApp'},
            ],
        })
        before_candidates = SellerLeadContactCandidate.objects.count()
        run_seller_lead_pipeline(
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        self.assertEqual(SellerLeadContactCandidate.objects.count(), before_candidates)

    def test_dry_run_does_not_change_existing_records(self):
        lead = self._existing_lead('pipeline_existing_dry', whatsapp='77011234567')
        client = self._mock_client({self.DISCOVERY_QUERY: [self._discovery_row('pipeline_new_dry_only')]})
        run_seller_lead_pipeline(lead_limit=1, max_queries_per_lead=1, dry_run=True, client=client)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77011234567')

    def test_skip_discovery(self):
        client = self._mock_client({})
        stats = run_seller_lead_pipeline(
            skip_discovery=True,
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        self.assertTrue(stats.discovery.skipped)
        self.assertEqual(stats.discovery.queries_executed, 0)

    def test_skip_enrichment(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_skip_enrich')],
        })
        stats = run_seller_lead_pipeline(
            skip_enrichment=True,
            lead_limit=1,
            dry_run=False,
            client=client,
        )
        lead = SellerLead.objects.get(instagram_username='pipeline_skip_enrich')
        self.assertTrue(stats.enrichment.skipped)
        self.assertEqual(lead.whatsapp, '')

    def test_lead_limit_respected(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [
                self._discovery_row('pipeline_limit_a'),
                self._discovery_row('pipeline_limit_b'),
                self._discovery_row('pipeline_limit_c'),
            ],
        })
        stats = run_seller_lead_pipeline(
            lead_limit=2,
            dry_run=False,
            client=client,
        )
        self.assertEqual(stats.discovery.new_profiles, 2)
        self.assertEqual(SellerLead.objects.filter(
            instagram_username__in=['pipeline_limit_a', 'pipeline_limit_b', 'pipeline_limit_c'],
        ).count(), 2)

    def test_search_limit_passed_to_brave_client(self):
        seen_counts: list[int] = []

        class CountingClient:
            def search(self, query, count=10):
                seen_counts.append(count)
                return []

        run_seller_lead_pipeline(
            search_limit=7,
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=True,
            client=CountingClient(),
        )
        self.assertIn(7, seen_counts)

    def test_max_queries_per_lead_respected(self):
        queries_seen: list[str] = []
        discovery_query = self.DISCOVERY_QUERY

        class CountingClient:
            def search(self, query, count=10):
                queries_seen.append(query)
                if query == discovery_query:
                    return [{
                        'title': 'Shop pipeline_query_limit',
                        'url': 'https://www.instagram.com/pipeline_query_limit/',
                        'description': 'Profile',
                    }]
                if 'pipeline_query_limit' in query:
                    return []
                return []

        run_seller_lead_pipeline(
            lead_limit=1,
            max_queries_per_lead=2,
            dry_run=False,
            client=CountingClient(),
        )
        enrichment_queries = [q for q in queries_seen if 'pipeline_query_limit' in q]
        self.assertEqual(len(enrichment_queries), 2)

    def test_one_lead_error_does_not_stop_others(self):
        call_count = {'value': 0}

        class PartialFailClient:
            def search(self, query, count=10):
                if query == self.DISCOVERY_QUERY:
                    return [
                        self._discovery_row('pipeline_fail_a'),
                        self._discovery_row('pipeline_fail_b'),
                    ]
                if 'pipeline_fail_a' in query:
                    call_count['value'] += 1
                    if call_count['value'] == 1:
                        raise SellerLeadSearchHTTPError('HTTP 500', status_code=500)
                if 'wa.me' in query or 'WhatsApp' in query:
                    return [{'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'}]
                return [{'title': 'x', 'url': 'https://www.instagram.com/pipeline_fail_b/', 'description': 'n/a'}]

        PartialFailClient.DISCOVERY_QUERY = self.DISCOVERY_QUERY
        PartialFailClient._discovery_row = staticmethod(self._discovery_row)
        stats = run_seller_lead_pipeline(
            lead_limit=2,
            max_queries_per_lead=1,
            dry_run=False,
            client=PartialFailClient(),
        )
        self.assertEqual(stats.enrichment.errors, 1)
        self.assertEqual(SellerLead.objects.filter(instagram_username='pipeline_fail_b').count(), 1)

    def test_status_not_changed(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_status_shop')],
            'site:instagram.com/pipeline_status_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        run_seller_lead_pipeline(lead_limit=1, max_queries_per_lead=1, dry_run=False, client=client)
        lead = SellerLead.objects.get(instagram_username='pipeline_status_shop')
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)

    def test_command_output_contains_discovery_and_enrichment(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_report_shop')],
            'site:instagram.com/pipeline_report_shop WhatsApp': [
                {'title': 'WA', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
            ],
        })
        stdout = StringIO()
        with patch(
            'core.management.commands.run_seller_lead_pipeline.run_seller_lead_pipeline',
            return_value=run_seller_lead_pipeline(
                lead_limit=1,
                max_queries_per_lead=1,
                dry_run=True,
                client=client,
            ),
        ):
            call_command(
                'run_seller_lead_pipeline',
                lead_limit=1,
                max_queries_per_lead=1,
                dry_run=True,
                stdout=stdout,
            )
        output = stdout.getvalue()
        self.assertIn('DISCOVERY:', output)
        self.assertIn('ENRICHMENT:', output)
        self.assertIn('Dry-run: база данных не изменялась.', output)

    def test_command_rejects_invalid_limit(self):
        with self.assertRaises(CommandError):
            call_command('run_seller_lead_pipeline', search_limit=0, dry_run=True)

    def test_api_key_not_logged(self):
        secret_key = 'BSA-valid-key-0123456789'
        payload = {'web': {'results': []}}

        def fake_urlopen(req, timeout=10):
            body = json.dumps(payload).encode('utf-8')
            headers = Message()
            headers['Content-Type'] = 'application/json'
            response = io.BytesIO(body)
            response.status = 200
            response.headers = headers
            return response

        client = BraveSearchClient(secret_key, urlopen=fake_urlopen)
        with self.assertLogs('core.services.seller_lead_search', level='INFO') as logs:
            client.search('query')
        self.assertNotIn(secret_key, '\n'.join(logs.output))

    def test_api_key_not_in_http_error(self):
        secret_key = 'BSA-valid-key-0123456789'
        headers = Message()
        headers['Content-Type'] = 'application/json'
        http_error = error.HTTPError(
            'https://api.search.brave.com/res/v1/web/search',
            401,
            'HTTP Error',
            hdrs=headers,
            fp=io.BytesIO(b'{"message":"Unauthorized"}'),
        )

        def fake_urlopen(req, timeout=10):
            raise http_error

        client = BraveSearchClient(secret_key, urlopen=fake_urlopen)
        with self.assertRaises(SellerLeadSearchHTTPError) as ctx:
            client.search('query')
        self.assertNotIn(secret_key, str(ctx.exception))

    def test_conflict_report_does_not_mark_primary_saved(self):
        client = self._mock_client({
            self.DISCOVERY_QUERY: [self._discovery_row('pipeline_conflict_report')],
            'site:instagram.com/pipeline_conflict_report WhatsApp': [
                {'title': 'A', 'url': 'https://wa.me/77011234567', 'description': 'WhatsApp'},
                {'title': 'B', 'url': 'https://wa.me/77019876543', 'description': 'WhatsApp'},
            ],
        })
        stats = run_seller_lead_pipeline(
            lead_limit=1,
            max_queries_per_lead=1,
            dry_run=True,
            client=client,
        )
        report = stats.enrichment.lead_reports[0]
        self.assertIn('conflict-candidate', report.action)
        self.assertNotIn('основной', report.action)
