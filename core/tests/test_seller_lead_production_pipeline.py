"""Production qualification, enrichment schedule, and the daily SellerLead report."""

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    BUSINESS_TYPE_DEALER,
    BUSINESS_TYPE_DISMANTLER,
    BUSINESS_TYPE_MIXED,
    BUSINESS_TYPE_NEW_PARTS,
    BUSINESS_TYPE_OTHER_AUTO,
    BUSINESS_TYPE_SERVICE_ONLY,
    BUSINESS_TYPE_SERVICE_PARTS,
    BUSINESS_TYPE_UNKNOWN,
    BUSINESS_TYPE_WHOLESALER,
    MARKET_SCOPE_FOREIGN,
    MARKET_SCOPE_KZ,
    MARKET_SCOPE_UNKNOWN,
    Seller,
    SellerLead,
    SellerLeadLocation,
    SellerLeadSource,
)
from core.services.seller_contact_enrichment import SellerContactEnrichmentError
from core.services.seller_lead_classification import classify_seller_lead
from core.services.seller_lead_daily_report import build_seller_lead_daily_report
from core.services.seller_lead_enrichment_schedule import (
    RESULT_AMBIGUOUS,
    RESULT_HTTP_403,
    RESULT_NETWORK_ERROR,
    RESULT_NO_CONTACTS,
    RESULT_VERIFIED_WHATSAPP,
    apply_enrichment_schedule,
    claim_due_seller_leads,
    due_seller_leads,
)
from core.services.seller_lead_qualification import (
    QUALIFICATION_FOREIGN,
    QUALIFICATION_NEEDS_REVIEW,
    QUALIFICATION_QUALIFIED,
    qualification_status,
)


def _lead(**kwargs):
    defaults = {'name': 'Omega Parts', 'city': 'Алматы'}
    defaults.update(kwargs)
    return SellerLead.objects.create(**defaults)


def _result(**kwargs):
    defaults = {
        'verified_whatsapp': [],
        'errors': [],
        'outcome': 'no_contacts',
        'observations': [],
        'pending_candidates': [],
        'source_runs': [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class ClassificationTests(TestCase):
    def _classify(self, **kwargs):
        lead = _lead(**kwargs)
        classify_seller_lead(lead)
        lead.refresh_from_db()
        return lead

    def test_auto_dismantler_is_its_own_type(self):
        lead = self._classify(profile_description='Авторазбор, контрактные запчасти')
        self.assertEqual(lead.business_type, BUSINESS_TYPE_DISMANTLER)
        self.assertEqual(lead.business_type, SellerLead.BUSINESS_TYPE_AUTO_DISMANTLER)
        self.assertGreater(lead.business_type_confidence, 0)
        self.assertIn('авторазбор', lead.business_type_evidence)
        self.assertIsNotNone(lead.last_classified_at)

    def test_parts_store(self):
        lead = self._classify(profile_description='Магазин автозапчастей, новые запчасти')
        self.assertEqual(lead.business_type, BUSINESS_TYPE_NEW_PARTS)
        self.assertEqual(lead.business_type, SellerLead.BUSINESS_TYPE_PARTS_STORE)

    def test_wholesaler(self):
        lead = self._classify(profile_description='Оптовый поставщик автозапчастей')
        self.assertEqual(lead.business_type, BUSINESS_TYPE_WHOLESALER)

    def test_service_only_is_not_a_parts_seller(self):
        lead = self._classify(profile_description='Автосервис, шиномонтаж')
        self.assertEqual(lead.business_type, BUSINESS_TYPE_SERVICE_ONLY)
        self.assertEqual(qualification_status(lead), QUALIFICATION_NEEDS_REVIEW)
        self.assertIsNone(lead.next_enrichment_at)

    def test_service_with_parts(self):
        lead = self._classify(profile_description='Автосервис, магазин автозапчастей')
        self.assertEqual(lead.business_type, BUSINESS_TYPE_SERVICE_PARTS)

    def test_mixed_new_and_used(self):
        lead = self._classify(profile_description='Авторазбор и магазин автозапчастей')
        self.assertEqual(lead.business_type, BUSINESS_TYPE_MIXED)

    def test_unknown_from_a_weak_word(self):
        lead = self._classify(profile_description='Продаём автозапчасти')
        self.assertEqual(lead.business_type, BUSINESS_TYPE_UNKNOWN)

    def test_dealer_without_parts_needs_review(self):
        lead = self._classify(profile_description='Дилерский центр')
        self.assertEqual(lead.business_type, BUSINESS_TYPE_OTHER_AUTO)
        self.assertEqual(qualification_status(lead), QUALIFICATION_NEEDS_REVIEW)

    def test_dealer_with_parts(self):
        lead = self._classify(profile_description='Официальный дилер, автозапчасти в наличии')
        self.assertEqual(lead.business_type, BUSINESS_TYPE_DEALER)

    def test_foreign_city_is_strong_evidence(self):
        lead = self._classify(city='Минск', profile_description='Магазин автозапчастей')
        self.assertEqual(lead.market_scope, MARKET_SCOPE_FOREIGN)
        self.assertIn('Минск', lead.market_scope_evidence)
        self.assertEqual(qualification_status(lead), QUALIFICATION_FOREIGN)
        self.assertIsNone(lead.next_enrichment_at)

    def test_foreign_domain_and_location(self):
        by_lead = self._classify(city='', website_url='https://shop.example.by/catalog')
        self.assertEqual(by_lead.market_scope, MARKET_SCOPE_FOREIGN)
        bishkek = _lead(city='', profile_description='Магазин автозапчастей')
        seen = timezone.now()
        SellerLeadLocation.objects.create(
            seller_lead=bishkek,
            city='Бишкек',
            first_seen_at=seen,
            last_seen_at=seen,
        )
        classify_seller_lead(bishkek)
        bishkek.refresh_from_db()
        self.assertEqual(bishkek.market_scope, MARKET_SCOPE_FOREIGN)

    def test_casual_foreign_mention_does_not_make_foreign(self):
        lead = self._classify(
            city='Алматы',
            profile_description='Магазин автозапчастей, доставка из Минска',
        )
        self.assertEqual(lead.market_scope, MARKET_SCOPE_KZ)
        self.assertEqual(qualification_status(lead), QUALIFICATION_QUALIFIED)
        empty = self._classify(city='', profile_description='Упоминание Бишкека в тексте')
        self.assertEqual(empty.market_scope, MARKET_SCOPE_UNKNOWN)

    def test_dry_run_writes_nothing(self):
        lead = _lead(profile_description='Авторазбор')
        planned = classify_seller_lead(lead, dry_run=True)
        lead.refresh_from_db()
        self.assertEqual(planned, BUSINESS_TYPE_DISMANTLER)
        self.assertEqual(lead.business_type, BUSINESS_TYPE_UNKNOWN)
        self.assertEqual(lead.market_scope, MARKET_SCOPE_UNKNOWN)
        self.assertIsNone(lead.last_classified_at)


class ScheduleTests(TestCase):
    def _due_parts_store(self, **kwargs):
        lead = _lead(profile_description='Магазин автозапчастей', **kwargs)
        classify_seller_lead(lead)
        lead.refresh_from_db()
        return lead

    def test_new_qualified_lead_is_due_now(self):
        lead = self._due_parts_store()
        self.assertIsNotNone(lead.next_enrichment_at)
        self.assertLessEqual(lead.next_enrichment_at, timezone.now())
        self.assertIn(lead, list(due_seller_leads()))

    def test_verified_whatsapp_waits_ninety_days(self):
        lead = self._due_parts_store()
        before = timezone.now()
        apply_enrichment_schedule(lead, RESULT_VERIFIED_WHATSAPP, now=before)
        lead.save()
        self.assertGreaterEqual(lead.next_enrichment_at, before + timedelta(days=90))
        self.assertNotIn(lead, list(due_seller_leads(before)))

    def test_no_contacts_then_repeat(self):
        lead = self._due_parts_store()
        moment = timezone.now()
        apply_enrichment_schedule(lead, RESULT_NO_CONTACTS, now=moment)
        self.assertEqual(lead.enrichment_attempt_count, 1)
        self.assertEqual(lead.next_enrichment_at, moment + timedelta(days=7))
        apply_enrichment_schedule(lead, RESULT_NO_CONTACTS, now=moment)
        self.assertEqual(lead.enrichment_attempt_count, 2)
        self.assertEqual(lead.next_enrichment_at, moment + timedelta(days=30))

    def test_network_and_forbidden_delays(self):
        lead = self._due_parts_store()
        moment = timezone.now()
        apply_enrichment_schedule(lead, RESULT_NETWORK_ERROR, now=moment)
        self.assertEqual(lead.enrichment_attempt_count, 0)
        self.assertEqual(lead.next_enrichment_at, moment + timedelta(days=1))
        apply_enrichment_schedule(lead, RESULT_HTTP_403, now=moment)
        self.assertEqual(lead.enrichment_attempt_count, 0)
        self.assertEqual(lead.next_enrichment_at, moment + timedelta(days=7))
        apply_enrichment_schedule(lead, RESULT_AMBIGUOUS, now=moment)
        self.assertEqual(lead.next_enrichment_at, moment + timedelta(days=7))

    def test_rejected_foreign_and_future_are_not_due(self):
        rejected = self._due_parts_store()
        rejected.lifecycle_status = SellerLead.LIFECYCLE_REJECTED
        rejected.save(update_fields=['lifecycle_status', 'updated_at'])
        foreign = self._due_parts_store(city='Минск', name='Minsk Parts')
        waiting = self._due_parts_store(name='Later Parts')
        waiting.next_enrichment_at = timezone.now() + timedelta(days=2)
        waiting.save(update_fields=['next_enrichment_at', 'updated_at'])
        due_ids = set(due_seller_leads().values_list('pk', flat=True))
        self.assertNotIn(rejected.pk, due_ids)
        self.assertNotIn(foreign.pk, due_ids)
        self.assertNotIn(waiting.pk, due_ids)

    def test_registered_seller_is_not_processed_again(self):
        seller = Seller.objects.create(name='Already in', whatsapp='77010000001', transport_type='car')
        lead = self._due_parts_store()
        lead.request_seller = seller
        lead.save(update_fields=['request_seller', 'updated_at'])
        self.assertNotIn(lead, list(due_seller_leads()))

    def test_claim_is_not_repeated_by_a_second_runner(self):
        moment = timezone.now()
        lead = self._due_parts_store()
        SellerLead.objects.filter(pk=lead.pk).update(next_enrichment_at=moment)
        first = claim_due_seller_leads(limit=25, now=moment)
        second = claim_due_seller_leads(limit=25, now=moment)
        self.assertEqual([item.pk for item in first], [lead.pk])
        self.assertEqual(second, [])
        lead.refresh_from_db()
        self.assertEqual(lead.next_enrichment_at, moment + timedelta(minutes=90))
        self.assertFalse(
            due_seller_leads(moment + timedelta(minutes=89)).filter(pk=lead.pk).exists(),
        )
        self.assertTrue(
            due_seller_leads(moment + timedelta(minutes=90)).filter(pk=lead.pk).exists(),
        )


class ClassifyBackfillTests(TestCase):
    def test_apply_without_a_selector_does_not_queue_existing_rows(self):
        lead = _lead(profile_description='Магазин автозапчастей')
        with self.assertRaises(CommandError):
            call_command('classify_seller_leads', '--apply')
        lead.refresh_from_db()
        self.assertIsNone(lead.next_enrichment_at)
        self.assertFalse(due_seller_leads().filter(pk=lead.pk).exists())

    def test_apply_arms_only_target_leads_with_an_empty_schedule(self):
        due = _lead(name='Due', profile_description='Магазин автозапчастей')
        foreign = _lead(name='Foreign', city='Минск', profile_description='Магазин автозапчастей')
        service = _lead(name='Service', profile_description='Автосервис')
        review = _lead(name='Review', profile_description='Дилерский центр')
        duplicate = _lead(
            name='Dup',
            profile_description='Магазин автозапчастей',
            lifecycle_status=SellerLead.LIFECYCLE_DUPLICATE,
        )
        rejected = _lead(
            name='Rejected',
            profile_description='Магазин автозапчастей',
            lifecycle_status=SellerLead.LIFECYCLE_REJECTED,
        )
        closed = _lead(
            name='Closed',
            profile_description='Магазин автозапчастей',
            lifecycle_status=SellerLead.LIFECYCLE_CLOSED,
        )
        seller = Seller.objects.create(name='Linked', whatsapp='77010000002', transport_type='car')
        linked = _lead(
            name='Linked',
            profile_description='Магазин автозапчастей',
            request_seller=seller,
        )
        future = timezone.now() + timedelta(days=3)
        scheduled = _lead(name='Scheduled', profile_description='Магазин автозапчастей')
        SellerLead.objects.filter(pk=scheduled.pk).update(next_enrichment_at=future)
        leads = [due, foreign, service, review, duplicate, rejected, closed, linked, scheduled]
        args = ['--limit', '20']
        for lead in leads:
            args.extend(['--lead-id', str(lead.pk)])
        call_command('classify_seller_leads', '--apply', *args)

        due.refresh_from_db()
        self.assertEqual(due.market_scope, MARKET_SCOPE_KZ)
        self.assertLessEqual(due.next_enrichment_at, timezone.now())
        self.assertTrue(due_seller_leads().filter(pk=due.pk).exists())

        scheduled.refresh_from_db()
        self.assertEqual(scheduled.next_enrichment_at, future)

        for lead in (foreign, service, review, duplicate, rejected, closed, linked):
            lead.refresh_from_db()
            self.assertIsNone(lead.next_enrichment_at, lead.name)
            self.assertFalse(due_seller_leads().filter(pk=lead.pk).exists())

    def test_historical_null_schedule_is_not_due_until_apply(self):
        lead = _lead(profile_description='Магазин автозапчастей')
        SellerLead.objects.filter(pk=lead.pk).update(
            business_type=BUSINESS_TYPE_NEW_PARTS,
            market_scope=MARKET_SCOPE_KZ,
            last_classified_at=timezone.now(),
            next_enrichment_at=None,
        )
        self.assertFalse(due_seller_leads().filter(pk=lead.pk).exists())
        call_command('classify_seller_leads', '--dry-run', '--lead-id', str(lead.pk), '--limit', '1')
        lead.refresh_from_db()
        self.assertIsNone(lead.next_enrichment_at)
        call_command('classify_seller_leads', '--apply', '--lead-id', str(lead.pk), '--limit', '1')
        lead.refresh_from_db()
        self.assertLessEqual(lead.next_enrichment_at, timezone.now())
        self.assertTrue(due_seller_leads().filter(pk=lead.pk).exists())


class SchedulerCommandTests(TestCase):
    def test_dry_run_does_not_write(self):
        lead = _lead(profile_description='Магазин автозапчастей')
        with patch(
            'core.management.commands.process_seller_lead_enrichment.enrich_seller_lead_contacts',
        ) as enrich:
            call_command('process_seller_lead_enrichment', '--dry-run', '--batch-size', '5')
        enrich.assert_not_called()
        lead.refresh_from_db()
        self.assertIsNone(lead.last_classified_at)
        self.assertIsNone(lead.next_enrichment_at)
        self.assertEqual(lead.market_scope, MARKET_SCOPE_UNKNOWN)

    def test_one_lead_error_does_not_stop_the_batch(self):
        first = _lead(name='First', profile_description='Магазин автозапчастей')
        second = _lead(name='Second', profile_description='Магазин автозапчастей')
        classify_seller_lead(first)
        classify_seller_lead(second)

        def fake_enrich(lead, **kwargs):
            if lead.pk == first.pk:
                raise RuntimeError('parser blew up')
            return _result()

        with patch(
            'core.management.commands.process_seller_lead_enrichment.enrich_seller_lead_contacts',
            side_effect=fake_enrich,
        ):
            out = StringIO()
            call_command('process_seller_lead_enrichment', '--apply', '--batch-size', '5', stdout=out)
        text = out.getvalue()
        self.assertIn(f'#{first.pk} failed error RuntimeError', text)
        self.assertIn(f'#{second.pk} {RESULT_NO_CONTACTS}', text)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.last_enrichment_result, 'error')
        self.assertEqual(second.last_enrichment_result, RESULT_NO_CONTACTS)
        self.assertGreater(second.next_enrichment_at, timezone.now())

    def test_configuration_error_stops_the_command(self):
        _lead(profile_description='Магазин автозапчастей')
        with patch(
            'core.management.commands.process_seller_lead_enrichment.enrich_seller_lead_contacts',
            side_effect=SellerContactEnrichmentError('SELLER_CONTACT_WEBSITE_ENABLED=False'),
        ):
            with self.assertRaises(CommandError):
                call_command('process_seller_lead_enrichment', '--apply', '--batch-size', '5')


class DailyReportTests(TestCase):
    def test_daily_counters_keep_business_types_apart(self):
        now = timezone.now()
        dismantler = _lead(name='Разбор', profile_description='Авторазбор')
        store = _lead(name='Магазин', profile_description='Магазин автозапчастей')
        wholesale = _lead(name='Опт', profile_description='Оптовый поставщик автозапчастей')
        service = _lead(name='СТО', profile_description='Автосервис')
        foreign = _lead(name='Minsk', city='Минск', profile_description='Автомойка')
        for lead in (dismantler, store, wholesale, service, foreign):
            classify_seller_lead(lead)
        rejected = _lead(name='Old', city='Астана')
        SellerLead.objects.filter(pk=rejected.pk).update(
            rejected_at=now,
            lifecycle_status=SellerLead.LIFECYCLE_REJECTED,
        )
        old = _lead(name='Yesterday', city='Шымкент')
        SellerLead.objects.filter(pk=old.pk).update(
            created_at=now - timedelta(days=3),
            last_classified_at=now - timedelta(days=3),
            business_type=BUSINESS_TYPE_NEW_PARTS,
            market_scope=MARKET_SCOPE_KZ,
            last_enrichment_attempt_at=now - timedelta(days=2),
            last_enrichment_result=RESULT_HTTP_403,
        )
        SellerLead.objects.filter(pk=store.pk).update(
            last_enrichment_attempt_at=now,
            last_enrichment_result=RESULT_VERIFIED_WHATSAPP,
        )
        SellerLead.objects.filter(pk=wholesale.pk).update(
            last_enrichment_attempt_at=now,
            last_enrichment_result=RESULT_NO_CONTACTS,
        )
        SellerLead.objects.filter(pk=dismantler.pk).update(
            last_enrichment_attempt_at=now,
            last_enrichment_result=RESULT_HTTP_403,
        )
        SellerLead.objects.filter(pk=rejected.pk).update(
            last_enrichment_result='error',
            last_enrichment_attempt_at=None,
            next_enrichment_at=None,
        )
        SellerLead.objects.filter(pk=service.pk).update(
            last_enrichment_result='error',
            last_enrichment_attempt_at=None,
            next_enrichment_at=now + timedelta(days=1),
        )
        text = build_seller_lead_daily_report(now=now)
        self.assertIn('новых SellerLead: 6', text)
        self.assertIn('классифицировано: 5', text)
        self.assertIn('auto_dismantler: 1', text)
        self.assertIn('parts_store: 1', text)
        self.assertIn('wholesaler: 1', text)
        self.assertIn('service/mixed: 1', text)
        self.assertIn('foreign: 1', text)
        self.assertIn('rejected/not-target: 1', text)
        self.assertIn('enrichment processed: 4', text)
        self.assertIn('verified WhatsApp найдено: 1', text)
        self.assertIn('no contacts: 1', text)
        self.assertIn('ambiguous: 0', text)
        self.assertIn('HTTP 403: 1', text)
        self.assertIn('timeout/network errors: 0', text)
        self.assertIn('failed/unexpected errors: 1', text)
        self.assertIn('всего SellerLead: 7', text)
        self.assertIn('Требует внимания', text)

    def test_command_prints_without_sending(self):
        out = StringIO()
        call_command('seller_lead_daily_report', stdout=out)
        self.assertIn('SellerLead — сводка за сутки', out.getvalue())
        self.assertIn('Требует внимания', out.getvalue())
