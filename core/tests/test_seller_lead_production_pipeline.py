"""Production qualification, enrichment schedule, and the daily SellerLead report."""

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
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
    Brand,
    Country,
    Seller,
    SellerLead,
    SellerLeadDuplicateMatch,
    SellerLeadEvidence,
    SellerLeadLocation,
    SellerLeadSource,
)
from core.services.seller_contact_enrichment import SellerContactEnrichmentError
from core.services.seller_contact_google_places import GooglePlaceLocator
from core.services.seller_discovery_sources import upsert_seller_lead_source
from core.services.seller_lead_classification import classify_seller_lead
from core.services.seller_lead_daily_report import build_seller_lead_daily_report
from core.services.seller_lead_enrichment_schedule import (
    RESULT_AMBIGUOUS,
    RESULT_CONFLICT,
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

    @override_settings(
        SELLER_CONTACT_ENRICHMENT_ENABLED=True,
        SELLER_CONTACT_GOOGLE_PLACES_ENABLED=True,
        GOOGLE_PLACES_API_KEY='google-secret-key',
        SELLER_CONTACT_WEBSITE_ENABLED=False,
        SELLER_CONTACT_BRAVE_ENABLED=False,
        SELLER_CONTACT_2GIS_ENABLED=False,
        SELLER_CONTACT_YANDEX_ENABLED=False,
    )
    def test_external_id_conflict_is_not_a_batch_failure(self):
        owner = _lead(name='Owner Parts')
        seen = timezone.now() - timedelta(days=1)
        source = upsert_seller_lead_source(
            owner,
            source_type=SellerLeadSource.SOURCE_GOOGLE_PLACES,
            provider='google_places',
            external_id='places/shared-owner',
            observed_at=seen,
        )
        source.refresh_from_db()
        owner_stamp = (source.seller_lead_id, source.external_id, source.last_seen_at, source.updated_at)
        SellerLead.objects.filter(pk=owner.pk).update(
            business_type=BUSINESS_TYPE_UNKNOWN,
            market_scope=MARKET_SCOPE_UNKNOWN,
            last_classified_at=timezone.now(),
            next_enrichment_at=None,
            lifecycle_status=SellerLead.LIFECYCLE_FOUND,
        )
        challenger = _lead(name='Challenger Parts', profile_description='Магазин автозапчастей')
        nxt = _lead(name='Next Parts', profile_description='Магазин автозапчастей')
        classify_seller_lead(challenger)
        classify_seller_lead(nxt)

        def locate(**kwargs):
            if kwargs.get('name') == 'Challenger Parts':
                return GooglePlaceLocator(place_id='places/shared-owner', website_uri='')
            return GooglePlaceLocator(place_id='', website_uri='')

        out = StringIO()
        with patch(
            'core.services.seller_contact_enrichment.locate_google_place',
            side_effect=locate,
        ):
            call_command('process_seller_lead_enrichment', '--apply', '--batch-size', '5', stdout=out)
        text = out.getvalue()
        source.refresh_from_db()
        owner.refresh_from_db()
        challenger.refresh_from_db()
        nxt.refresh_from_db()
        self.assertEqual(
            (source.seller_lead_id, source.external_id, source.last_seen_at, source.updated_at),
            owner_stamp,
        )
        self.assertEqual(SellerLeadSource.objects.filter(external_id='places/shared-owner').count(), 1)
        self.assertFalse(
            SellerLeadSource.objects.filter(seller_lead=challenger, external_id='places/shared-owner').exists(),
        )
        match = SellerLeadDuplicateMatch.objects.get()
        self.assertEqual(match.status, SellerLeadDuplicateMatch.STATUS_POSSIBLE)
        self.assertIn('external_id', match.reasons)
        self.assertIsNone(owner.duplicate_of_id)
        self.assertIsNone(challenger.duplicate_of_id)
        self.assertNotEqual(challenger.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertEqual(challenger.last_enrichment_result, RESULT_CONFLICT)
        self.assertEqual(challenger.enrichment_attempt_count, 0)
        self.assertGreater(challenger.next_enrichment_at, timezone.now() + timedelta(days=2))
        self.assertNotIn('failed error', text)
        self.assertIn(f'#{challenger.pk} {RESULT_CONFLICT}', text)
        self.assertIn(f'#{nxt.pk} {RESULT_NO_CONTACTS}', text)
        self.assertEqual(nxt.last_enrichment_result, RESULT_NO_CONTACTS)


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


class ProductionTitleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(name='Германия')
        Brand.objects.create(country=country, name='BMW', transport_type='car')
        Brand.objects.create(country=country, name='Mercedes-Benz', transport_type='car')

    def _type(self, name):
        lead = _lead(name=name, profile_description='')
        classify_seller_lead(lead)
        lead.refresh_from_db()
        return lead.business_type

    def test_production_titles(self):
        expected = {
            'Автозапчасти алматы # и оптом (@avtozapchastialmaty_optom)': BUSINESS_TYPE_WHOLESALER,
            'ОПТОВЫЕ ПРОДАЖИ АВТОЗАПЧАСТЕЙ | Алматы (@omega_auto_parts)': BUSINESS_TYPE_WHOLESALER,
            'ЗАПЧАСТИ ДЛЯ BMW В АЛМАТЫ (@bmwpartsalmaty)': BUSINESS_TYPE_NEW_PARTS,
            'Круглосут.автомагазин в Алматы (@kaz_avto.kz)': BUSINESS_TYPE_UNKNOWN,
            'Авто-рынок жибек жол 15ряд 85к (@avtoshopkz1)': BUSINESS_TYPE_UNKNOWN,
            'АВТОЗАПЧАСТИ | НА ЗАКАЗ ИЗ США | АЛМАТЫ (@usa.carparts)': BUSINESS_TYPE_NEW_PARTS,
            'Запчасти | Пятый Элемент (@fealmaty)': BUSINESS_TYPE_UNKNOWN,
            'Запчасти MERCEDES-BENZ (@mb_parts_kz)': BUSINESS_TYPE_NEW_PARTS,
            'Авторазбор В Алматы (@avtorazbor__kz)': BUSINESS_TYPE_DISMANTLER,
            'авторазбор с Европы в Алматы (@avtorazbor_audi_volksvagen)': BUSINESS_TYPE_DISMANTLER,
            'АВТОАКСЕССУАРЫ ОПТОМ!!! (@optomkzru)': BUSINESS_TYPE_OTHER_AUTO,
            'АВТОЗАПЧАСТИ ШЫМКЕНТ/ОПТОМ (@donix.kz)': BUSINESS_TYPE_WHOLESALER,
            'АВТО и МОТО запчасти Алматы (@4motokz)': BUSINESS_TYPE_UNKNOWN,
            'BMW Алматы (@bavaria_almaty)': BUSINESS_TYPE_UNKNOWN,
            'ЗАПЧАСТИ НА MERCEDES•АЛМАТЫ (@mbshop.kz)': BUSINESS_TYPE_NEW_PARTS,
        }
        for name, business_type in expected.items():
            self.assertEqual(self._type(name), business_type, name)

    def test_lone_parts_word_stays_unknown(self):
        self.assertEqual(self._type('Продаём автозапчасти'), BUSINESS_TYPE_UNKNOWN)

    def test_dismantler_plus_new_parts_stays_mixed(self):
        self.assertEqual(
            self._type('Авторазбор и магазин автозапчастей'),
            BUSINESS_TYPE_MIXED,
        )

    def test_wholesale_phrase_without_parts_is_not_a_parts_store(self):
        self.assertNotEqual(self._type('Доставка оптом по городу'), BUSINESS_TYPE_NEW_PARTS)
        self.assertNotEqual(self._type('Доставка оптом по городу'), BUSINESS_TYPE_WHOLESALER)


class ForeignIdentityTests(TestCase):
    def test_minsk_name_and_handle_override_discovery_city(self):
        lead = _lead(
            city='Алматы',
            name='audi.minsk.garage Minsk',
            instagram_username='audi.minsk.garage',
        )
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.market_scope, MARKET_SCOPE_FOREIGN)
        self.assertIsNone(lead.next_enrichment_at)

    def test_bishkek_name_alone_conflicts_with_discovery_city(self):
        lead = _lead(city='Алматы', name='Автозапчасти БИШКЕК')
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.market_scope, MARKET_SCOPE_UNKNOWN)
        self.assertNotEqual(lead.market_scope, MARKET_SCOPE_KZ)

    def test_bishkek_name_and_kg_domain_are_foreign(self):
        lead = _lead(
            city='Алматы',
            name='Автозапчасти БИШКЕК',
            website_url='https://shop.example.kg/catalog',
        )
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.market_scope, MARKET_SCOPE_FOREIGN)

    def test_minsk_name_and_by_domain_are_foreign(self):
        lead = _lead(
            city='Алматы',
            name='Minsk Garage',
            website_url='https://parts.example.by/catalog',
        )
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.market_scope, MARKET_SCOPE_FOREIGN)

    def test_by_domain_alone_does_not_become_kazakhstan(self):
        lead = _lead(city='Алматы', name='Omega Parts', website_url='https://shop.example.by/')
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.market_scope, MARKET_SCOPE_UNKNOWN)

    def test_passing_minsk_mention_stays_kazakhstan(self):
        lead = _lead(
            city='Алматы',
            name='Магазин автозапчастей',
            profile_description='Доставка из Минска',
        )
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.market_scope, MARKET_SCOPE_KZ)
        self.assertEqual(lead.business_type, BUSINESS_TYPE_NEW_PARTS)

    def test_search_evidence_mention_does_not_make_foreign(self):
        lead = _lead(city='Алматы', name='Магазин автозапчастей')
        SellerLeadEvidence.objects.create(
            seller_lead=lead,
            field_name='profile',
            value='Результат поиска: магазин в Минске',
            observed_at=timezone.now(),
        )
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.market_scope, MARKET_SCOPE_KZ)
