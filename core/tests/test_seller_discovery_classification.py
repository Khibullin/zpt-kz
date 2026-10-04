from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.models import Product, SellerProfile
from core.admin import (
    SellerLeadAdmin,
    SellerLeadConfidenceFilter,
    SellerLeadDiscoveredBrandFilter,
    SellerLeadDiscoveredCategoryFilter,
    SellerLeadDiscoveredModelFilter,
    SellerLeadHasWhatsAppFilter,
    SellerLeadSourceProviderFilter,
    SellerLeadWhatsAppStateFilter,
)
from core.models import (
    Brand,
    CarModel,
    Country,
    PartCategory,
    Seller,
    SellerLead,
    SellerLeadContactCandidate,
    SellerLeadEvidence,
    SellerLeadSource,
)
from core.services.seller_discovery_ingestion import ingest_seller_discovery_hit
from core.services.seller_discovery_providers.base import (
    DiscoveryProviderError,
    SellerDiscoveryHit,
)
from core.services.seller_discovery_providers.catalog import (
    DISCOVERY_CITIES,
    KZ_DISCOVERY_CITIES,
    KZ_DISCOVERY_CITY_NAMES,
    TWO_GIS_MAX_RADIUS_M,
    TWO_GIS_MIN_RADIUS_M,
    resolve_city,
)
from core.services.seller_contact_enrichment import (
    EnrichmentObservation,
    SellerContactEnrichmentError,
    _apply,
    enrich_seller_lead_contacts,
)
from core.services.seller_contact_google_places import GooglePlaceLocator, GooglePlacesError
from core.services.seller_discovery_runner import SellerDiscoveryRunError, run_seller_discovery
from core.services.seller_lead_classification import classify_seller_lead
from core.services.seller_lead_classification_selection import (
    UNKNOWN_CLASSIFICATION_RETRY_DAYS,
    select_leads_needing_classification,
)
from core.services.seller_lead_enrichment_selection import select_leads_needing_enrichment
from core.services.seller_lead_whatsapp_state import (
    WHATSAPP_CONFLICT,
    WHATSAPP_NOT_FOUND,
    WHATSAPP_PENDING,
    WHATSAPP_VERIFIED,
    filter_seller_leads_by_whatsapp_state,
    seller_lead_whatsapp_state,
)


REQUIRED_CITIES = (
    'Алматы',
    'Астана',
    'Шымкент',
    'Караганда',
    'Актобе',
    'Тараз',
    'Павлодар',
    'Усть-Каменогорск',
    'Семей',
    'Костанай',
    'Кызылорда',
    'Атырау',
    'Актау',
    'Уральск',
    'Петропавловск',
    'Туркестан',
    'Талдыкорган',
    'Кокшетау',
    'Жезказган',
    'Конаев',
)


def _lead(**kwargs):
    defaults = {'name': 'Omega Parts', 'city': 'Алматы'}
    defaults.update(kwargs)
    return SellerLead.objects.create(**defaults)


def _hit(**kwargs):
    defaults = {
        'provider': 'two_gis',
        'source_type': SellerLeadSource.SOURCE_TWO_GIS,
        'external_id': '70000001000000991',
        'name': 'Omega Parts',
        'city': 'Алматы',
        'address': '',
        'latitude': None,
        'longitude': None,
        'phone': '',
        'phones': (),
        'website': '',
        'instagram_url': '',
        'category_text': '',
        'rubrics': (),
        'source_url': '',
        'search_query': 'авторазбор',
        'org_id': '',
        'confidence': 70,
        'raw_data': {'id': '70000001000000991'},
        'observed_at': timezone.now(),
    }
    defaults.update(kwargs)
    return SellerDiscoveryHit(**defaults)


class _QuietProvider:
    name = 'two_gis'

    def __init__(self):
        self.calls = []

    def search(self, *, city, direction, limit=None, max_pages=None):
        self.calls.append((city, direction))
        return []


class BusinessTypeTests(TestCase):
    def test_new_parts_classification(self):
        lead = _lead(profile_description='Магазин автозапчастей, официальный поставщик')
        self.assertEqual(classify_seller_lead(lead), SellerLead.BUSINESS_TYPE_NEW_PARTS)
        lead.refresh_from_db()
        self.assertEqual(lead.business_type_confidence, 85)
        self.assertIn('магазин автозапчастей', lead.business_type_evidence)

    def test_dismantler_classification(self):
        lead = _lead(profile_description='Контрактные запчасти с разбора')
        self.assertEqual(classify_seller_lead(lead), SellerLead.BUSINESS_TYPE_DISMANTLER)

    def test_mixed_classification(self):
        lead = _lead(profile_description='Магазин автозапчастей и контрактные запчасти')
        self.assertEqual(classify_seller_lead(lead), SellerLead.BUSINESS_TYPE_MIXED)

    def test_unknown_when_only_generic_word(self):
        lead = _lead(name='Omega', profile_description='автозапчасти')
        self.assertEqual(classify_seller_lead(lead), SellerLead.BUSINESS_TYPE_UNKNOWN)
        lead.refresh_from_db()
        self.assertIn('автозапчасти', lead.business_type_evidence)
        self.assertEqual(lead.business_type_confidence, 0)

    def test_search_query_alone_does_not_set_business_type(self):
        ingest_seller_discovery_hit(_hit(name='Omega Parts', search_query='авторазбор'))
        lead = SellerLead.objects.get()
        self.assertEqual(lead.business_type, SellerLead.BUSINESS_TYPE_UNKNOWN)
        self.assertEqual(lead.whatsapp, '')

    def test_whatsapp_absent_lead_is_still_ingested(self):
        before_sellers = Seller.objects.count()
        ingest_seller_discovery_hit(_hit(
            name='Авторазбор Север',
            phone='',
            phones=(),
            search_query='автозапчасти',
        ))
        lead = SellerLead.objects.get()
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.business_type, SellerLead.BUSINESS_TYPE_DISMANTLER)
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_FOUND)
        self.assertEqual(Seller.objects.count(), before_sellers)
        self.assertEqual(SellerProfile.objects.count(), 0)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(Product.objects.count(), 0)


class AssortmentTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай')
        self.chery = Brand.objects.create(country=country, name='Chery')
        self.toyota = Brand.objects.create(country=country, name='Toyota')
        self.hyundai = Brand.objects.create(country=country, name='Hyundai')
        self.tiggo = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro')
        CarModel.objects.create(brand=self.chery, name='7')
        CarModel.objects.create(brand=self.toyota, name='Accent')
        CarModel.objects.create(brand=self.hyundai, name='Accent')
        self.filters = PartCategory.objects.create(name='Масляные фильтры')

    def test_structured_brand_and_model_match(self):
        lead = _lead(profile_description='Chery Tiggo 7 Pro, масляные фильтры, магазин автозапчастей')
        classify_seller_lead(lead)
        self.assertEqual(list(lead.discovered_brands.values_list('name', flat=True)), ['Chery'])
        self.assertEqual(list(lead.discovered_models.values_list('name', flat=True)), ['Tiggo 7 Pro'])
        self.assertEqual(list(lead.discovered_categories.values_list('name', flat=True)), ['Масляные фильтры'])
        link = lead.brand_links.get()
        self.assertEqual(link.source_kind, 'profile')
        self.assertGreaterEqual(link.confidence, 70)
        self.assertIsNotNone(link.observed_at)

    def test_ambiguous_short_model_is_rejected(self):
        lead = _lead(profile_description='Chery 7 и Accent, магазин автозапчастей')
        classify_seller_lead(lead)
        self.assertEqual(list(lead.discovered_brands.values_list('name', flat=True)), ['Chery'])
        self.assertEqual(list(lead.discovered_models.values_list('pk', flat=True)), [])

    def test_category_match_does_not_create_missing_category(self):
        before = PartCategory.objects.count()
        lead = _lead(profile_description='Редкие турбины, которых нет в справочнике')
        classify_seller_lead(lead)
        self.assertEqual(PartCategory.objects.count(), before)
        self.assertEqual(lead.discovered_categories.count(), 0)
        self.assertEqual(Brand.objects.count(), 3)
        self.assertEqual(CarModel.objects.count(), 4)
        self.assertTrue(PartCategory.objects.filter(pk=self.filters.pk).exists())


class WhatsAppStateTests(TestCase):
    def test_states_and_admin_filter(self):
        verified = _lead(name='Verified', whatsapp='77001112233')
        SellerLeadEvidence.objects.create(
            seller_lead=verified,
            field_name='whatsapp',
            value='77001112233',
            normalized_value='77001112233',
            is_selected=True,
            observed_at=timezone.now(),
        )
        pending = _lead(name='Pending', whatsapp='77002223344')
        missing = _lead(name='Missing', whatsapp='')
        conflict = _lead(name='Conflict', whatsapp='')
        SellerLeadContactCandidate.objects.create(
            seller_lead=conflict,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77003334455',
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        self.assertEqual(seller_lead_whatsapp_state(verified), WHATSAPP_VERIFIED)
        self.assertEqual(seller_lead_whatsapp_state(pending), WHATSAPP_PENDING)
        self.assertEqual(seller_lead_whatsapp_state(missing), WHATSAPP_NOT_FOUND)
        self.assertEqual(seller_lead_whatsapp_state(conflict), WHATSAPP_CONFLICT)

        admin = SellerLeadAdmin(SellerLead, AdminSite())
        self.assertIn('business_type', admin.list_filter)
        self.assertIn('city', admin.list_filter)
        self.assertIn('lifecycle_status', admin.list_filter)
        self.assertIn(SellerLeadWhatsAppStateFilter, admin.list_filter)
        self.assertIn(SellerLeadHasWhatsAppFilter, admin.list_filter)
        self.assertIn(SellerLeadDiscoveredBrandFilter, admin.list_filter)
        self.assertIn(SellerLeadDiscoveredModelFilter, admin.list_filter)
        self.assertIn(SellerLeadDiscoveredCategoryFilter, admin.list_filter)
        self.assertIn(SellerLeadSourceProviderFilter, admin.list_filter)
        self.assertIn(SellerLeadConfidenceFilter, admin.list_filter)
        self.assertIn('business_type', admin.list_display)
        self.assertIn('brands_summary', admin.list_display)
        self.assertIn('categories_summary', admin.list_display)
        state_filter = SellerLeadWhatsAppStateFilter(
            None,
            {'whatsapp_state': [WHATSAPP_NOT_FOUND]},
            SellerLead,
            admin,
        )
        self.assertEqual(list(state_filter.queryset(None, SellerLead.objects.all())), [missing])
        verified_filter = SellerLeadWhatsAppStateFilter(
            None,
            {'whatsapp_state': [WHATSAPP_VERIFIED]},
            SellerLead,
            admin,
        )
        self.assertEqual(list(verified_filter.queryset(None, SellerLead.objects.all())), [verified])


class GeographyAndBatchTests(TestCase):
    def test_all_kz_cities_registry_is_bounded(self):
        self.assertEqual(set(DISCOVERY_CITIES), {'Алматы', 'Астана', 'Шымкент'})
        self.assertEqual(DISCOVERY_CITIES['Алматы'].latitude, 43.238293)
        for name in REQUIRED_CITIES:
            city = resolve_city(name)
            self.assertEqual(city.name, name)
            self.assertGreaterEqual(city.radius_m, TWO_GIS_MIN_RADIUS_M)
            self.assertLessEqual(city.radius_m, TWO_GIS_MAX_RADIUS_M)
            self.assertGreater(city.latitude, 40)
            self.assertLess(city.latitude, 56)
            self.assertGreater(city.longitude, 46)
            self.assertLess(city.longitude, 88)
        self.assertEqual(len(KZ_DISCOVERY_CITY_NAMES), len(REQUIRED_CITIES))
        self.assertEqual(len(KZ_DISCOVERY_CITIES), len(REQUIRED_CITIES))
        with self.assertRaises(DiscoveryProviderError):
            resolve_city('Берлин')

    def test_dry_run_writes_nothing_and_queries_stay_bounded(self):
        provider = _QuietProvider()
        before = (
            SellerLead.objects.count(),
            Seller.objects.count(),
            Product.objects.count(),
        )
        stats = run_seller_discovery(
            provider_names=['two_gis'],
            cities=list(KZ_DISCOVERY_CITY_NAMES),
            max_queries=2,
            max_hits=5,
            dry_run=True,
            providers=[provider],
            city_limit=5,
        )
        self.assertEqual(
            (
                SellerLead.objects.count(),
                Seller.objects.count(),
                Product.objects.count(),
            ),
            before,
        )
        self.assertLessEqual(stats.queries_executed, 2)
        self.assertEqual(len(provider.calls), stats.queries_executed)
        self.assertLessEqual(len(stats.cities), 5)
        self.assertLessEqual(len(stats.cities_processed), 2)
        self.assertEqual(stats.new_parts, 0)
        self.assertEqual(stats.dismantlers, 0)

    def test_same_business_type_does_not_merge_leads(self):
        first = _lead(name='Shop One', profile_description='Магазин автозапчастей')
        second = _lead(name='Shop Two', city='Астана', profile_description='Магазин автозапчастей')
        classify_seller_lead(first)
        classify_seller_lead(second)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(SellerLead.objects.count(), 2)
        self.assertIsNone(first.duplicate_of_id)
        self.assertIsNone(second.duplicate_of_id)
        self.assertEqual(first.lifecycle_status, SellerLead.LIFECYCLE_FOUND)

    def test_classification_does_not_regress_lifecycle(self):
        lead = _lead(
            profile_description='Новые запчасти в наличии',
            lifecycle_status=SellerLead.LIFECYCLE_ENRICHED,
        )
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_ENRICHED)
        self.assertIsNotNone(lead.last_classified_at)
        self.assertEqual(lead.business_type, SellerLead.BUSINESS_TYPE_NEW_PARTS)


class NeedsEnrichmentTests(TestCase):
    def test_fresh_verified_lead_is_excluded_and_missing_whatsapp_is_selected(self):
        verified = _lead(name='Fresh', whatsapp='77001112233', last_enriched_at=timezone.now())
        SellerLeadEvidence.objects.create(
            seller_lead=verified,
            field_name='whatsapp',
            value='77001112233',
            normalized_value='77001112233',
            is_selected=True,
            observed_at=timezone.now(),
        )
        fresh_checked = _lead(
            name='Checked',
            whatsapp='',
            last_enrichment_attempt_at=timezone.now(),
        )
        missing = _lead(name='Needs', whatsapp='', city='Караганда', business_type=SellerLead.BUSINESS_TYPE_DISMANTLER)
        closed = _lead(
            name='Closed',
            whatsapp='',
            lifecycle_status=SellerLead.LIFECYCLE_CLOSED,
        )
        selected = list(select_leads_needing_enrichment(city='Караганда', business_type='dismantler', limit=5))
        self.assertEqual(selected, [missing])
        self.assertNotIn(verified, selected)
        self.assertNotIn(fresh_checked, selected)
        self.assertNotIn(closed, selected)

    def test_stale_unverified_lead_can_be_selected_again(self):
        lead = _lead(
            name='Stale',
            whatsapp='',
            last_enrichment_attempt_at=timezone.now() - timedelta(days=31),
        )
        self.assertEqual(list(select_leads_needing_enrichment(limit=5)), [lead])

    def test_command_uses_needs_enrichment_selection(self):
        missing = _lead(name='Needs command', whatsapp='')
        _lead(
            name='Fresh command',
            whatsapp='77005556677',
            last_enrichment_attempt_at=timezone.now(),
        )
        result = SimpleNamespace(
            outcome='no_contacts',
            observations=[],
            dry_run=True,
            conflicts=[],
            verified_whatsapp=[],
            pending_candidates=[],
            source_runs=[],
            locators=[],
            websites_discovered=[],
            websites_considered=[],
            websites_skipped_brave_cap=[],
            websites_skipped_blocked=[],
            websites_skipped_budget=[],
            two_gis_external_id='',
            errors=[],
        )
        with patch(
            'core.management.commands.enrich_seller_contacts.enrich_seller_lead_contacts',
            return_value=result,
        ) as enrich:
            call_command(
                'enrich_seller_contacts',
                '--needs-enrichment',
                '--city',
                'Алматы',
                '--limit',
                '5',
                '--business-type',
                'unknown',
                '--source',
                'website',
                '--dry-run',
            )
        self.assertEqual(enrich.call_args.args[0].pk, missing.pk)


class CityBatchTests(TestCase):
    def _run(self, offset, limit=3):
        provider = _QuietProvider()
        stats = run_seller_discovery(
            provider_names=['two_gis'],
            cities=list(KZ_DISCOVERY_CITY_NAMES),
            max_queries=1,
            dry_run=True,
            providers=[provider],
            city_limit=limit,
            city_offset=offset,
        )
        return provider, stats

    def test_offset_zero_returns_the_first_cities(self):
        provider, stats = self._run(0, 3)
        self.assertEqual(stats.cities, list(KZ_DISCOVERY_CITY_NAMES[:3]))
        self.assertEqual(stats.city_offset, 0)
        self.assertEqual(stats.city_limit, 3)
        self.assertEqual(provider.calls[0][0], KZ_DISCOVERY_CITY_NAMES[0])

    def test_offset_returns_the_next_batch_and_repeat_is_stable(self):
        first_provider, first = self._run(3, 3)
        second_provider, second = self._run(3, 3)
        expected = list(KZ_DISCOVERY_CITY_NAMES[3:6])
        self.assertEqual(first.cities, expected)
        self.assertEqual(second.cities, expected)
        self.assertNotEqual(first.cities, list(KZ_DISCOVERY_CITY_NAMES[:3]))
        self.assertEqual(first_provider.calls[0][0], expected[0])
        self.assertEqual(second_provider.calls[0][0], expected[0])

    def test_out_of_range_offset_does_not_search_or_write(self):
        provider = _QuietProvider()
        before = SellerLead.objects.count()
        with self.assertRaises(SellerDiscoveryRunError):
            run_seller_discovery(
                provider_names=['two_gis'],
                cities=list(KZ_DISCOVERY_CITY_NAMES),
                dry_run=True,
                providers=[provider],
                city_limit=3,
                city_offset=len(KZ_DISCOVERY_CITY_NAMES) + 3,
            )
        self.assertEqual(provider.calls, [])
        self.assertEqual(SellerLead.objects.count(), before)

    def test_dry_run_batch_writes_nothing(self):
        provider = _QuietProvider()
        before = SellerLead.objects.count()
        stats = run_seller_discovery(
            provider_names=['two_gis'],
            cities=list(KZ_DISCOVERY_CITY_NAMES),
            max_queries=2,
            dry_run=True,
            providers=[provider],
            city_limit=3,
            city_offset=6,
        )
        self.assertEqual(stats.cities, list(KZ_DISCOVERY_CITY_NAMES[6:9]))
        self.assertEqual(SellerLead.objects.count(), before)
        self.assertEqual(len(provider.calls), stats.queries_executed)


class ClassifyCommandTests(TestCase):
    def test_classify_existing_lead_promotes_found_and_enriched(self):
        found = _lead(name='Found Shop', profile_description='Магазин автозапчастей')
        enriched = _lead(
            name='Enriched Shop',
            city='Астана',
            profile_description='Новые запчасти в наличии',
            lifecycle_status=SellerLead.LIFECYCLE_ENRICHED,
        )
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            call_command('classify_seller_leads', '--city', 'Алматы', '--apply', '--limit', '5')
            call_command('classify_seller_leads', '--lead-id', str(enriched.pk), '--apply')
        found.refresh_from_db()
        enriched.refresh_from_db()
        self.assertEqual(found.business_type, SellerLead.BUSINESS_TYPE_NEW_PARTS)
        self.assertEqual(found.lifecycle_status, SellerLead.LIFECYCLE_CLASSIFIED)
        self.assertIsNotNone(found.last_classified_at)
        self.assertEqual(enriched.lifecycle_status, SellerLead.LIFECYCLE_CLASSIFIED)
        self.assertEqual(enriched.business_type, SellerLead.BUSINESS_TYPE_NEW_PARTS)

    def test_later_lifecycle_does_not_regress(self):
        lead = _lead(
            profile_description='Магазин автозапчастей',
            lifecycle_status=SellerLead.LIFECYCLE_VERIFIED,
        )
        call_command('classify_seller_leads', '--lead-id', str(lead.pk), '--apply')
        lead.refresh_from_db()
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_VERIFIED)
        self.assertEqual(lead.business_type, SellerLead.BUSINESS_TYPE_NEW_PARTS)

    def test_dry_run_writes_nothing(self):
        lead = _lead(profile_description='Магазин автозапчастей')
        call_command('classify_seller_leads', '--lead-id', str(lead.pk), '--dry-run')
        lead.refresh_from_db()
        self.assertEqual(lead.business_type, SellerLead.BUSINESS_TYPE_UNKNOWN)
        self.assertIsNone(lead.last_classified_at)
        self.assertEqual(lead.discovered_brands.count(), 0)

    def test_needs_classification_rules(self):
        missing_stamp = _lead(
            name='No stamp',
            business_type=SellerLead.BUSINESS_TYPE_NEW_PARTS,
            last_classified_at=None,
        )
        fresh = _lead(
            name='Fresh class',
            city='Астана',
            profile_description='Магазин автозапчастей',
            lifecycle_status=SellerLead.LIFECYCLE_FOUND,
        )
        call_command('classify_seller_leads', '--lead-id', str(fresh.pk), '--apply')
        fresh.refresh_from_db()
        closed = _lead(
            name='Closed class',
            city='Шымкент',
            business_type=SellerLead.BUSINESS_TYPE_UNKNOWN,
            lifecycle_status=SellerLead.LIFECYCLE_CLOSED,
        )
        self.assertIn(missing_stamp, list(select_leads_needing_classification(limit=20)))
        self.assertNotIn(fresh, list(select_leads_needing_classification(city='Астана', limit=20)))
        self.assertNotIn(closed, list(select_leads_needing_classification(limit=20)))

        SellerLeadEvidence.objects.create(
            seller_lead=fresh,
            field_name='profile',
            value='Контрактные запчасти',
            observed_at=fresh.last_classified_at + timedelta(minutes=5),
        )
        self.assertIn(fresh, list(select_leads_needing_classification(city='Астана', limit=20)))

        stamped = _lead(name='Source later', city='Караганда', profile_description='Магазин автозапчастей')
        call_command('classify_seller_leads', '--lead-id', str(stamped.pk), '--apply')
        stamped.refresh_from_db()
        self.assertNotIn(stamped, list(select_leads_needing_classification(city='Караганда', limit=20)))
        now = timezone.now()
        SellerLeadSource.objects.create(
            seller_lead=stamped,
            source_type=SellerLeadSource.SOURCE_WEBSITE,
            provider='website',
            display_name='Официальный поставщик',
            source_url='https://later-shop.kz/',
            first_seen_at=now,
            last_seen_at=stamped.last_classified_at + timedelta(minutes=5),
        )
        self.assertIn(stamped, list(select_leads_needing_classification(city='Караганда', limit=20)))

    def test_command_without_mode_does_nothing(self):
        lead = _lead(profile_description='Магазин автозапчастей')
        with self.assertRaises(CommandError):
            call_command('classify_seller_leads', '--lead-id', str(lead.pk))
        lead.refresh_from_db()
        self.assertIsNone(lead.last_classified_at)


class NeedsClassificationSelectionTests(TestCase):
    def _ids(self):
        return set(select_leads_needing_classification(limit=20).values_list('pk', flat=True))

    def _stamp(self, lead, *, business_type, when):
        lead.business_type = business_type
        lead.last_classified_at = when
        lead.save(update_fields=['business_type', 'last_classified_at', 'updated_at'])
        return lead

    def _source(self, lead, *, last_seen_at, fetched_at=None):
        return SellerLeadSource.objects.create(
            seller_lead=lead,
            source_type=SellerLeadSource.SOURCE_WEBSITE,
            provider='website',
            display_name='shop',
            source_url=f'https://shop-{lead.pk}.kz/',
            first_seen_at=last_seen_at,
            last_seen_at=last_seen_at,
            fetched_at=fetched_at,
        )

    def test_fresh_unknown_without_new_data_is_excluded(self):
        lead = self._stamp(
            _lead(name='Fresh unknown'),
            business_type=SellerLead.BUSINESS_TYPE_UNKNOWN,
            when=timezone.now(),
        )
        self.assertNotIn(lead.pk, self._ids())

    def test_unknown_without_classification_stamp_is_selected(self):
        lead = _lead(name='Never classified', business_type=SellerLead.BUSINESS_TYPE_UNKNOWN)
        self.assertIsNone(lead.last_classified_at)
        self.assertIn(lead.pk, self._ids())

    def test_unknown_with_newer_evidence_is_selected(self):
        classified_at = timezone.now() - timedelta(hours=1)
        lead = self._stamp(
            _lead(name='Unknown evidence'),
            business_type=SellerLead.BUSINESS_TYPE_UNKNOWN,
            when=classified_at,
        )
        SellerLeadEvidence.objects.create(
            seller_lead=lead,
            field_name='profile',
            value='Контрактные запчасти',
            observed_at=classified_at + timedelta(minutes=5),
        )
        self.assertIn(lead.pk, self._ids())

    def test_unknown_with_newer_source_last_seen_is_selected(self):
        classified_at = timezone.now() - timedelta(hours=1)
        lead = self._stamp(
            _lead(name='Unknown seen'),
            business_type=SellerLead.BUSINESS_TYPE_UNKNOWN,
            when=classified_at,
        )
        self._source(lead, last_seen_at=classified_at + timedelta(minutes=5))
        self.assertIn(lead.pk, self._ids())

    def test_unknown_with_newer_source_fetched_at_is_selected(self):
        classified_at = timezone.now() - timedelta(hours=1)
        lead = self._stamp(
            _lead(name='Unknown fetched'),
            business_type=SellerLead.BUSINESS_TYPE_UNKNOWN,
            when=classified_at,
        )
        self._source(
            lead,
            last_seen_at=classified_at - timedelta(days=1),
            fetched_at=classified_at + timedelta(minutes=5),
        )
        self.assertIn(lead.pk, self._ids())

    def test_unknown_older_than_retry_interval_is_selected(self):
        lead = self._stamp(
            _lead(name='Stale unknown'),
            business_type=SellerLead.BUSINESS_TYPE_UNKNOWN,
            when=timezone.now() - timedelta(days=UNKNOWN_CLASSIFICATION_RETRY_DAYS + 1),
        )
        self.assertIn(lead.pk, self._ids())

    def test_fresh_known_classification_is_excluded(self):
        lead = self._stamp(
            _lead(name='Fresh known'),
            business_type=SellerLead.BUSINESS_TYPE_NEW_PARTS,
            when=timezone.now(),
        )
        self.assertNotIn(lead.pk, self._ids())

    def test_known_classification_with_newer_evidence_is_selected(self):
        classified_at = timezone.now() - timedelta(hours=1)
        lead = self._stamp(
            _lead(name='Known evidence'),
            business_type=SellerLead.BUSINESS_TYPE_DISMANTLER,
            when=classified_at,
        )
        SellerLeadEvidence.objects.create(
            seller_lead=lead,
            field_name='profile',
            value='Магазин автозапчастей',
            observed_at=classified_at + timedelta(minutes=5),
        )
        self.assertIn(lead.pk, self._ids())


class AssortmentProvenanceTests(TestCase):
    def test_structured_assortment_keeps_source_when_source_row_is_deleted(self):
        country = Country.objects.create(name='Китай')
        chery = Brand.objects.create(country=country, name='Chery')
        CarModel.objects.create(brand=chery, name='Tiggo 7 Pro')
        filters = PartCategory.objects.create(name='Масляные фильтры')
        lead = _lead(name='Omega Parts', profile_description='')
        now = timezone.now()
        source = SellerLeadSource.objects.create(
            seller_lead=lead,
            source_type=SellerLeadSource.SOURCE_WEBSITE,
            provider='website',
            source_url='https://chery-shop.kz/about',
            display_name='Chery Tiggo 7 Pro, масляные фильтры',
            first_seen_at=now,
            last_seen_at=now,
        )
        evidence = SellerLeadEvidence.objects.create(
            seller_lead=lead,
            source=source,
            field_name='profile',
            value='Магазин автозапчастей Chery Tiggo 7 Pro, масляные фильтры',
            observed_at=now,
        )
        classify_seller_lead(lead, promote_lifecycle=True)
        lead.refresh_from_db()
        brand_link = lead.brand_links.get()
        self.assertEqual(brand_link.source_id, source.pk)
        self.assertEqual(brand_link.evidence_id, evidence.pk)
        self.assertEqual(lead.model_links.get().source_id, source.pk)
        self.assertEqual(lead.category_links.get().category_id, filters.pk)
        self.assertEqual(lead.category_links.get().source_id, source.pk)
        self.assertEqual(lead.business_type_source_id, source.pk)
        self.assertEqual(lead.business_type_evidence_item_id, evidence.pk)
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_CLASSIFIED)
        link_pk = brand_link.pk
        source.delete()
        brand_link.refresh_from_db()
        self.assertIsNone(brand_link.source_id)
        self.assertEqual(brand_link.brand_id, chery.pk)
        self.assertTrue(lead.brand_links.filter(pk=link_pk).exists())


class _FailProvider:
    name = 'two_gis'

    def __init__(self):
        self.calls = []

    def search(self, *, city, direction, limit=None, max_pages=None):
        self.calls.append((city, direction))
        raise DiscoveryProviderError('provider down')


class _HitProvider:
    name = 'two_gis'

    def __init__(self):
        self.calls = []

    def search(self, *, city, direction, limit=None, max_pages=None):
        self.calls.append((city, direction))
        return [_hit(external_id=f'hit-{len(self.calls)}', phone='', phones=(), website='', instagram_url='')]


class CityCompletionTests(TestCase):
    def _run(self, **kwargs):
        provider = kwargs.pop('provider', None) or _QuietProvider()
        providers = kwargs.pop('providers', [provider])
        stats = run_seller_discovery(
            provider_names=['two_gis'] * len(providers),
            cities=list(KZ_DISCOVERY_CITY_NAMES),
            dry_run=True,
            providers=providers,
            **kwargs,
        )
        return providers[0], stats

    def test_default_budget_completes_three_cities(self):
        provider, stats = self._run(city_limit=3, city_offset=0)
        self.assertEqual(stats.cities_selected, list(KZ_DISCOVERY_CITY_NAMES[:3]))
        self.assertEqual(stats.cities_completed, list(KZ_DISCOVERY_CITY_NAMES[:3]))
        self.assertEqual(stats.next_city_offset, 3)
        self.assertEqual(stats.queries_executed, 9)
        self.assertEqual(len(provider.calls), 9)

    def test_two_providers_complete_three_cities_within_budget(self):
        first = _QuietProvider()
        second = _QuietProvider()
        second.name = 'brave'
        stats = run_seller_discovery(
            provider_names=['two_gis', 'brave'],
            cities=list(KZ_DISCOVERY_CITY_NAMES),
            dry_run=True,
            providers=[first, second],
            city_limit=3,
            city_offset=0,
        )
        self.assertEqual(stats.cities_completed, list(KZ_DISCOVERY_CITY_NAMES[:3]))
        self.assertEqual(stats.next_city_offset, 3)
        self.assertEqual(stats.queries_executed, 18)
        self.assertEqual(len(first.calls) + len(second.calls), 18)

    def test_explicit_query_cap_advances_only_completed_cities(self):
        provider, stats = self._run(city_limit=3, city_offset=0, max_queries=4)
        self.assertEqual(stats.cities_selected, list(KZ_DISCOVERY_CITY_NAMES[:3]))
        self.assertEqual(stats.cities_completed, [KZ_DISCOVERY_CITY_NAMES[0]])
        self.assertEqual(stats.next_city_offset, 1)
        self.assertEqual(provider.calls[0][0], KZ_DISCOVERY_CITY_NAMES[0])
        self.assertEqual(provider.calls[-1][0], KZ_DISCOVERY_CITY_NAMES[1])
        self.assertNotIn(KZ_DISCOVERY_CITY_NAMES[1], stats.cities_completed)

    def test_partial_second_city_does_not_advance_past_it(self):
        _, first = self._run(city_limit=3, city_offset=0, max_queries=4)
        self.assertEqual(first.next_city_offset, 1)
        provider, second = self._run(city_limit=3, city_offset=first.next_city_offset, max_queries=4)
        self.assertEqual(second.cities_selected[0], KZ_DISCOVERY_CITY_NAMES[1])
        self.assertEqual(second.cities_completed, [KZ_DISCOVERY_CITY_NAMES[1]])
        self.assertEqual(provider.calls[0][0], KZ_DISCOVERY_CITY_NAMES[1])

    def test_sequential_next_offset_does_not_skip_cities(self):
        offset = 0
        completed = []
        for _step in range(3):
            _provider, stats = self._run(city_limit=3, city_offset=offset, max_queries=4)
            self.assertEqual(len(stats.cities_completed), 1)
            completed.extend(stats.cities_completed)
            self.assertEqual(stats.next_city_offset, offset + 1)
            offset = stats.next_city_offset
        self.assertEqual(completed, list(KZ_DISCOVERY_CITY_NAMES[:3]))
        self.assertEqual(offset, 3)

    def test_provider_error_advances_the_query_cursor(self):
        provider = _FailProvider()
        stats = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы'],
            directions=['один', 'два', 'три'],
            max_queries=1,
            dry_run=True,
            providers=[provider],
        )
        self.assertEqual(stats.errors, 1)
        self.assertEqual(stats.queries_executed, 1)
        self.assertEqual(stats.cities_completed, [])
        self.assertEqual(stats.next_city_offset, 0)
        self.assertEqual(stats.next_query_offset, 1)
        resumed = _FailProvider()
        second = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы'],
            directions=['один', 'два', 'три'],
            max_queries=1,
            query_offset=stats.next_query_offset,
            dry_run=True,
            providers=[resumed],
        )
        self.assertEqual(resumed.calls, [('Алматы', 'два')])
        self.assertEqual(second.next_query_offset, 2)

    def test_provider_error_counts_as_a_completed_query(self):
        provider = _FailProvider()
        stats = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы'],
            dry_run=True,
            providers=[provider],
        )
        self.assertEqual(stats.queries_executed, 3)
        self.assertEqual(stats.errors, 3)
        self.assertEqual(stats.cities_completed, ['Алматы'])
        self.assertEqual(stats.next_city_offset, 1)
        self.assertEqual(stats.next_query_offset, 0)

    def test_hit_cap_mid_city_does_not_complete_the_city(self):
        provider = _HitProvider()
        stats = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы', 'Астана'],
            dry_run=True,
            providers=[provider],
            max_hits=1,
            city_limit=2,
        )
        self.assertEqual(stats.hits_received, 1)
        self.assertEqual(stats.cities_completed, [])
        self.assertEqual(stats.next_city_offset, 0)
        self.assertEqual(stats.next_query_offset, 1)
        self.assertEqual(provider.calls[0][0], 'Алматы')

    @override_settings(
        SELLER_DISCOVERY_ENABLED=True,
        SELLER_DISCOVERY_2GIS_ENABLED=True,
        TWO_GIS_API_KEY='test-key',
    )
    def test_command_prints_next_city_offset(self):
        out = __import__('io').StringIO()
        with patch(
            'core.services.seller_discovery_runner.build_discovery_provider',
            return_value=_QuietProvider(),
        ):
            call_command(
                'discover_seller_sources',
                '--provider', 'two_gis',
                '--all-kz-cities',
                '--city-limit', '3',
                '--max-queries', '4',
                '--dry-run',
                stdout=out,
            )
        text = out.getvalue()
        self.assertIn('cities_selected=3', text)
        self.assertIn('cities_completed=1', text)
        self.assertIn('query_offset=0', text)
        self.assertIn('queries_per_city=3', text)
        self.assertIn('next_city_offset=1', text)
        self.assertIn('next_query_offset=1', text)
        self.assertIn('--city-offset 1 --query-offset 1', text)
        self.assertNotIn('offset + limit', text)


@override_settings(
    SELLER_CONTACT_ENRICHMENT_ENABLED=True,
    SELLER_CONTACT_WEBSITE_ENABLED=True,
    SELLER_CONTACT_GOOGLE_PLACES_ENABLED=True,
    GOOGLE_PLACES_API_KEY='test-key',
)
class EnrichmentAttemptTests(TestCase):
    def test_no_contacts_apply_stamps_attempt_only(self):
        lead = _lead(name='Empty Shop', whatsapp='')
        result = enrich_seller_lead_contacts(lead, sources=['website'], dry_run=False)
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'no_contacts')
        self.assertIsNotNone(lead.last_enrichment_attempt_at)
        self.assertIsNone(lead.last_enriched_at)

    def test_ambiguous_apply_stamps_attempt_only(self):
        lead = _lead(name='Ambiguous Shop', whatsapp='')
        locator = GooglePlaceLocator(place_id='', website_uri='', ambiguous=True, error='неоднозначно')
        with patch(
            'core.services.seller_contact_enrichment.locate_google_place',
            return_value=locator,
        ):
            result = enrich_seller_lead_contacts(lead, sources=['google_places'], dry_run=False)
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'ambiguous_google')
        self.assertIsNotNone(lead.last_enrichment_attempt_at)
        self.assertIsNone(lead.last_enriched_at)

    def test_successful_apply_stamps_attempt_and_enriched(self):
        lead = _lead(name='Useful Shop', whatsapp='', website_url='')
        _apply(lead, observations=[], place_id='', website_url='https://shop.kz/')
        lead.refresh_from_db()
        self.assertIsNotNone(lead.last_enrichment_attempt_at)
        self.assertIsNotNone(lead.last_enriched_at)
        self.assertEqual(lead.website_url, 'https://shop.kz/')

    def test_conflict_apply_stamps_attempt(self):
        lead = _lead(name='Conflict Shop', whatsapp='')
        _apply(
            lead,
            observations=[EnrichmentObservation(
                field_name='whatsapp',
                value='77001112233',
                confidence=90,
                explicit_whatsapp=True,
                source_url='https://example.kz/wa',
                excerpt='whatsapp',
                confirms_whatsapp=True,
                conflicting=True,
                origin='website',
            )],
            place_id='',
            website_url='',
        )
        lead.refresh_from_db()
        self.assertIsNotNone(lead.last_enrichment_attempt_at)
        self.assertEqual(lead.whatsapp, '')

    def test_dry_run_does_not_stamp_attempt(self):
        lead = _lead(name='Dry Shop', whatsapp='')
        enrich_seller_lead_contacts(lead, sources=['website'], dry_run=True)
        lead.refresh_from_db()
        self.assertIsNone(lead.last_enrichment_attempt_at)
        self.assertIsNone(lead.last_enriched_at)

    def test_failed_exception_does_not_stamp_attempt(self):
        lead = _lead(name='Broken Shop', whatsapp='')
        with patch(
            'core.services.seller_contact_enrichment.locate_google_place',
            side_effect=GooglePlacesError('down'),
        ):
            with self.assertRaises(SellerContactEnrichmentError):
                enrich_seller_lead_contacts(lead, sources=['google_places'], dry_run=False)
        lead.refresh_from_db()
        self.assertIsNone(lead.last_enrichment_attempt_at)

    def test_apply_exception_rolls_back_attempt(self):
        lead = _lead(name='Rollback Shop', whatsapp='')
        with patch(
            'core.services.seller_contact_enrichment.refresh_seller_lead_identity',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                _apply(lead, observations=[], place_id='', website_url='https://shop.kz/')
        lead.refresh_from_db()
        self.assertIsNone(lead.last_enrichment_attempt_at)
        self.assertEqual(lead.website_url, '')

    def test_needs_enrichment_skips_fresh_attempt_and_selects_next(self):
        blocked = _lead(name='Blocked', whatsapp='', last_enrichment_attempt_at=timezone.now())
        nxt = _lead(name='Next', whatsapp='')
        selected = list(select_leads_needing_enrichment(limit=1))
        self.assertEqual(selected, [nxt])
        self.assertNotIn(blocked, selected)

    def test_stale_unsuccessful_attempt_is_selected_again(self):
        fresh = _lead(name='Fresh attempt', whatsapp='', last_enrichment_attempt_at=timezone.now())
        stale = _lead(
            name='Stale attempt',
            whatsapp='',
            last_enrichment_attempt_at=timezone.now() - timedelta(days=31),
        )
        selected = list(select_leads_needing_enrichment(limit=5))
        self.assertIn(stale, selected)
        self.assertNotIn(fresh, selected)

    def test_recent_enriched_at_does_not_block_a_missing_attempt(self):
        lead = _lead(name='Enriched clock', whatsapp='', last_enriched_at=timezone.now())
        self.assertEqual(list(select_leads_needing_enrichment(limit=1)), [lead])


class NewestEvidenceTests(TestCase):
    def test_classification_reads_the_newest_evidence_inside_the_cap(self):
        lead = _lead(name='Omega Parts', profile_description='')
        old = timezone.now() - timedelta(days=10)
        SellerLeadEvidence.objects.bulk_create([
            SellerLeadEvidence(
                seller_lead=lead,
                field_name='profile',
                value='каталог',
                observed_at=old,
            )
            for _index in range(41)
        ])
        SellerLeadEvidence.objects.create(
            seller_lead=lead,
            field_name='profile',
            value='Контрактные запчасти с разбора',
            observed_at=timezone.now(),
        )
        self.assertEqual(classify_seller_lead(lead), SellerLead.BUSINESS_TYPE_DISMANTLER)


class SourcedProvenanceTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай')
        self.chery = Brand.objects.create(country=country, name='Chery')
        self.filters = PartCategory.objects.create(name='Масляные фильтры')

    def test_sourced_evidence_wins_over_the_same_direct_text(self):
        lead = _lead(
            name='Магазин автозапчастей',
            category='Chery масляные фильтры',
            profile_description='',
        )
        now = timezone.now()
        source = SellerLeadSource.objects.create(
            seller_lead=lead,
            source_type=SellerLeadSource.SOURCE_WEBSITE,
            provider='website',
            source_url='https://chery-shop.kz/about',
            display_name='старое название',
            first_seen_at=now,
            last_seen_at=now,
        )
        evidence = SellerLeadEvidence.objects.create(
            seller_lead=lead,
            source=source,
            field_name='profile',
            value='Магазин автозапчастей Chery, масляные фильтры',
            observed_at=now,
        )
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.business_type_source_id, source.pk)
        self.assertEqual(lead.business_type_evidence_item_id, evidence.pk)
        brand_link = lead.brand_links.get()
        self.assertEqual(brand_link.brand_id, self.chery.pk)
        self.assertEqual(brand_link.source_id, source.pk)
        self.assertEqual(brand_link.evidence_id, evidence.pk)
        category_link = lead.category_links.get()
        self.assertEqual(category_link.category_id, self.filters.pk)
        self.assertEqual(category_link.source_id, source.pk)
        self.assertEqual(category_link.evidence_id, evidence.pk)

    def test_canonical_only_lead_keeps_null_provenance(self):
        lead = _lead(profile_description='Магазин автозапчастей')
        classify_seller_lead(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.business_type, SellerLead.BUSINESS_TYPE_NEW_PARTS)
        self.assertIsNone(lead.business_type_source_id)
        self.assertIsNone(lead.business_type_evidence_item_id)


class WhatsAppAdminQueryTests(TestCase):
    def _states(self, limit):
        request = RequestFactory().get('/admin/core/sellerlead/')
        admin_model = SellerLeadAdmin(SellerLead, AdminSite())
        with CaptureQueriesContext(connection) as ctx:
            rows = list(admin_model.get_queryset(request)[:limit])
            labels = [admin_model.whatsapp_state_display(row) for row in rows]
        return len(ctx.captured_queries), labels

    def test_changelist_whatsapp_state_does_not_cascade_per_row(self):
        verified = _lead(name='Verified row', whatsapp='77001112233')
        SellerLeadEvidence.objects.create(
            seller_lead=verified,
            field_name='whatsapp',
            value='77001112233',
            normalized_value='77001112233',
            is_selected=True,
            observed_at=timezone.now(),
        )
        conflict = _lead(name='Conflict row', whatsapp='')
        SellerLeadContactCandidate.objects.create(
            seller_lead=conflict,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77002223344',
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        pending = _lead(name='Pending row', whatsapp='')
        SellerLeadContactCandidate.objects.create(
            seller_lead=pending,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77003334455',
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_PENDING,
        )
        for index in range(17):
            _lead(name=f'Plain {index}', whatsapp='')

        queries_10, labels_10 = self._states(10)
        queries_20, labels_20 = self._states(20)
        self.assertEqual(queries_10, queries_20)
        self.assertLess(queries_20, 15)
        self.assertEqual(len(labels_20), 20)
        self.assertIn('VERIFIED', labels_20)
        self.assertIn('CONFLICT', labels_20)
        self.assertIn('PENDING', labels_20)
        self.assertIn('NOT_FOUND', labels_20)

    def test_whatsapp_filter_uses_one_sql_query(self):
        for index in range(20):
            _lead(name=f'Filter {index}', whatsapp='')
        with CaptureQueriesContext(connection) as ctx:
            rows = list(filter_seller_leads_by_whatsapp_state(
                SellerLead.objects.all(),
                WHATSAPP_NOT_FOUND,
            ))
        self.assertEqual(len(rows), 20)
        self.assertEqual(len(ctx.captured_queries), 1)


class ManualWhatsAppApprovalTests(TestCase):
    def test_approve_whatsapp_creates_selected_evidence_and_verifies(self):
        lead = _lead(name='Manual WA', whatsapp='')
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77001112233',
            confidence='high',
            source_url='https://shop.kz/contacts',
            source_text='whatsapp 77001112233',
            status=SellerLeadContactCandidate.STATUS_PENDING,
        )
        candidate.approve_as_primary()
        lead.refresh_from_db()
        candidate.refresh_from_db()
        evidence = SellerLeadEvidence.objects.get(seller_lead=lead, field_name='whatsapp')
        self.assertEqual(lead.whatsapp, '77001112233')
        self.assertEqual(candidate.source_url, 'https://shop.kz/contacts')
        self.assertEqual(candidate.source_text, 'whatsapp 77001112233')
        self.assertEqual(evidence.value, '77001112233')
        self.assertTrue(evidence.is_selected)
        self.assertFalse(evidence.is_owner_verified)
        self.assertEqual(evidence.extraction_method, SellerLeadEvidence.METHOD_MANUAL)
        self.assertEqual(evidence.confidence, 100)
        self.assertEqual(seller_lead_whatsapp_state(lead), WHATSAPP_VERIFIED)

    def test_repeat_approve_does_not_duplicate_evidence(self):
        lead = _lead(name='Repeat WA', whatsapp='')
        existing = SellerLeadEvidence.objects.create(
            seller_lead=lead,
            field_name='whatsapp',
            value='77001112233',
            normalized_value='77001112233',
            extraction_method=SellerLeadEvidence.METHOD_PARSER,
            is_selected=False,
            observed_at=timezone.now() - timedelta(days=5),
        )
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77001112233',
            confidence='medium',
            status=SellerLeadContactCandidate.STATUS_PENDING,
        )
        candidate.approve_as_primary()
        candidate.approve_as_primary()
        evidences = SellerLeadEvidence.objects.filter(seller_lead=lead, field_name='whatsapp')
        self.assertEqual(evidences.count(), 1)
        existing.refresh_from_db()
        self.assertTrue(existing.is_selected)
        self.assertEqual(existing.extraction_method, SellerLeadEvidence.METHOD_PARSER)

    def test_approved_phone_does_not_become_verified_whatsapp(self):
        lead = _lead(name='Phone only', whatsapp='')
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_PHONE,
            value='77004445566',
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_PENDING,
        )
        candidate.approve_as_primary()
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')
        self.assertFalse(SellerLeadEvidence.objects.filter(seller_lead=lead, field_name='whatsapp').exists())
        self.assertEqual(seller_lead_whatsapp_state(lead), WHATSAPP_NOT_FOUND)

    def test_approved_primary_without_evidence_is_verified(self):
        lead = _lead(name='Legacy approval', whatsapp='77001112233')
        SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77001112233',
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_APPROVED,
            is_primary=True,
            reviewed_at=timezone.now(),
        )
        self.assertEqual(seller_lead_whatsapp_state(lead), WHATSAPP_VERIFIED)

    def test_conflict_outranks_verified_whatsapp(self):
        lead = _lead(name='Conflict wins', whatsapp='77001112233')
        SellerLeadEvidence.objects.create(
            seller_lead=lead,
            field_name='whatsapp',
            value='77001112233',
            normalized_value='77001112233',
            is_selected=True,
            observed_at=timezone.now(),
        )
        SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77009998877',
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        self.assertEqual(seller_lead_whatsapp_state(lead), WHATSAPP_CONFLICT)


class EnrichmentFreshnessAndOrderTests(TestCase):
    def test_never_attempted_leads_sort_before_stale_retries(self):
        older = _lead(
            name='Older retry',
            whatsapp='',
            last_enrichment_attempt_at=timezone.now() - timedelta(days=60),
        )
        newer = _lead(
            name='Newer retry',
            whatsapp='',
            last_enrichment_attempt_at=timezone.now() - timedelta(days=31),
        )
        first_null = _lead(name='Never A', whatsapp='')
        second_null = _lead(name='Never B', whatsapp='')
        selected = select_leads_needing_enrichment(limit=10)
        self.assertIn('NULLS FIRST', str(selected.query))
        self.assertEqual(list(selected), [first_null, second_null, older, newer])

    def test_fresh_verified_evidence_is_excluded_and_stale_is_selected(self):
        fresh = _lead(name='Fresh verified', whatsapp='77001112233')
        SellerLeadEvidence.objects.create(
            seller_lead=fresh,
            field_name='whatsapp',
            value='77001112233',
            normalized_value='77001112233',
            is_selected=True,
            observed_at=timezone.now(),
        )
        stale = _lead(name='Stale verified', whatsapp='77002223344')
        SellerLeadEvidence.objects.create(
            seller_lead=stale,
            field_name='whatsapp',
            value='77002223344',
            normalized_value='77002223344',
            is_selected=True,
            observed_at=timezone.now() - timedelta(days=45),
        )
        selected = list(select_leads_needing_enrichment(limit=10))
        self.assertNotIn(fresh, selected)
        self.assertIn(stale, selected)
        self.assertEqual(seller_lead_whatsapp_state(stale), WHATSAPP_VERIFIED)
        self.assertEqual(seller_lead_whatsapp_state(fresh), WHATSAPP_VERIFIED)

    def test_fresh_approved_primary_is_excluded_and_stale_approval_is_selected(self):
        fresh = _lead(name='Fresh approval', whatsapp='77001112233')
        SellerLeadContactCandidate.objects.create(
            seller_lead=fresh,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77001112233',
            confidence='low',
            status=SellerLeadContactCandidate.STATUS_APPROVED,
            is_primary=True,
            reviewed_at=timezone.now(),
        )
        stale = _lead(name='Stale approval', whatsapp='77002223344')
        SellerLeadContactCandidate.objects.create(
            seller_lead=stale,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77002223344',
            confidence='low',
            status=SellerLeadContactCandidate.STATUS_APPROVED,
            is_primary=True,
            reviewed_at=timezone.now() - timedelta(days=45),
        )
        selected = list(select_leads_needing_enrichment(limit=10))
        self.assertNotIn(fresh, selected)
        self.assertIn(stale, selected)
        self.assertEqual(seller_lead_whatsapp_state(fresh), WHATSAPP_VERIFIED)
        self.assertEqual(seller_lead_whatsapp_state(stale), WHATSAPP_VERIFIED)

    def test_attempt_clock_without_whatsapp(self):
        fresh_failure = _lead(
            name='Failed today',
            whatsapp='',
            last_enrichment_attempt_at=timezone.now(),
        )
        stale_failure = _lead(
            name='Failed last month',
            whatsapp='',
            last_enrichment_attempt_at=timezone.now() - timedelta(days=31),
        )
        selected = list(select_leads_needing_enrichment(limit=10))
        self.assertNotIn(fresh_failure, selected)
        self.assertIn(stale_failure, selected)


class _DenseProvider:
    name = 'two_gis'

    def __init__(self, hits_per_query=10):
        self.calls = []
        self.hits_per_query = hits_per_query

    def search(self, *, city, direction, limit=None, max_pages=None):
        self.calls.append((city, direction))
        serial = len(self.calls)
        return [
            _hit(
                external_id=f'{serial}-{index}',
                name=f'Shop {serial}-{index}',
                phone='',
                phones=(),
                website='',
                instagram_url='',
                source_url='',
            )
            for index in range(self.hits_per_query)
        ]


class DiscoveryCursorTests(TestCase):
    def test_dense_city_resumes_at_the_next_query_and_does_not_repeat(self):
        directions = ['автозапчасти', 'магазин автозапчастей', 'китайские запчасти']
        before = SellerLead.objects.count()
        first_provider = _DenseProvider()
        first = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы'],
            directions=directions,
            max_hits=20,
            dry_run=True,
            providers=[first_provider],
        )
        self.assertEqual(first.hits_received, 20)
        self.assertEqual(first.queries_executed, 2)
        self.assertEqual(first.cities_completed, [])
        self.assertEqual(first.next_city_offset, 0)
        self.assertEqual(first.next_query_offset, 2)
        self.assertEqual(first.queries_per_city, 3)
        self.assertEqual(SellerLead.objects.count(), before)
        self.assertEqual([call[1] for call in first_provider.calls], directions[:2])

        second_provider = _DenseProvider()
        second = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы'],
            directions=directions,
            max_hits=20,
            query_offset=first.next_query_offset,
            city_offset=first.next_city_offset,
            dry_run=True,
            providers=[second_provider],
        )
        self.assertEqual(second_provider.calls, [('Алматы', directions[2])])
        self.assertEqual(second.cities_completed, ['Алматы'])
        self.assertEqual(second.next_city_offset, 1)
        self.assertEqual(second.next_query_offset, 0)
        self.assertEqual(SellerLead.objects.count(), before)

    def test_query_cap_resumes_inside_the_city(self):
        directions = ['один', 'два', 'три']
        provider = _QuietProvider()
        stats = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы'],
            directions=directions,
            max_queries=2,
            dry_run=True,
            providers=[provider],
        )
        self.assertEqual([call[1] for call in provider.calls], directions[:2])
        self.assertEqual(stats.next_city_offset, 0)
        self.assertEqual(stats.next_query_offset, 2)
        resumed = _QuietProvider()
        second = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы'],
            directions=directions,
            query_offset=2,
            dry_run=True,
            providers=[resumed],
        )
        self.assertEqual(resumed.calls, [('Алматы', 'три')])
        self.assertEqual(second.cities_completed, ['Алматы'])
        self.assertEqual(second.next_query_offset, 0)
        self.assertEqual(second.next_city_offset, 1)

    def test_large_plan_progresses_in_capped_runs(self):
        directions = [f'направление {index}' for index in range(15)]
        first = _QuietProvider()
        second_provider = _QuietProvider()
        second_provider.name = 'brave'
        stats = run_seller_discovery(
            provider_names=['two_gis', 'brave'],
            cities=['Алматы'],
            directions=directions,
            dry_run=True,
            providers=[first, second_provider],
        )
        self.assertEqual(stats.queries_per_city, 30)
        self.assertEqual(stats.queries_executed, 20)
        self.assertEqual(stats.cities_completed, [])
        self.assertEqual(stats.next_query_offset, 20)
        self.assertEqual(stats.next_city_offset, 0)
        follow_a = _QuietProvider()
        follow_b = _QuietProvider()
        follow_b.name = 'brave'
        done = run_seller_discovery(
            provider_names=['two_gis', 'brave'],
            cities=['Алматы'],
            directions=directions,
            query_offset=20,
            dry_run=True,
            providers=[follow_a, follow_b],
        )
        self.assertEqual(done.queries_executed, 10)
        self.assertEqual(done.cities_completed, ['Алматы'])
        self.assertEqual(done.next_query_offset, 0)
        self.assertEqual(done.next_city_offset, 1)
        self.assertEqual(len(first.calls) + len(second_provider.calls), 20)
        self.assertEqual(len(follow_a.calls) + len(follow_b.calls), 10)

    def test_partial_second_city_keeps_both_cursors(self):
        directions = ['один', 'два', 'три']
        stats = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы', 'Астана', 'Шымкент'],
            directions=directions,
            max_queries=5,
            dry_run=True,
            providers=[_QuietProvider()],
            city_limit=2,
        )
        self.assertEqual(stats.cities_completed, ['Алматы'])
        self.assertEqual(stats.next_city_offset, 1)
        self.assertEqual(stats.next_query_offset, 2)
        resumed = _QuietProvider()
        second = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы', 'Астана', 'Шымкент'],
            directions=directions,
            max_queries=1,
            city_offset=stats.next_city_offset,
            query_offset=stats.next_query_offset,
            dry_run=True,
            providers=[resumed],
            city_limit=2,
        )
        self.assertEqual(resumed.calls, [('Астана', 'три')])
        self.assertEqual(second.cities_completed, ['Астана'])
        self.assertEqual(second.next_query_offset, 0)

    def test_query_offset_past_the_plan_does_not_search(self):
        provider = _QuietProvider()
        before = SellerLead.objects.count()
        with self.assertRaises(SellerDiscoveryRunError):
            run_seller_discovery(
                provider_names=['two_gis'],
                cities=['Алматы'],
                directions=['один', 'два', 'три'],
                query_offset=3,
                dry_run=True,
                providers=[provider],
            )
        with self.assertRaises(SellerDiscoveryRunError):
            run_seller_discovery(
                provider_names=['two_gis'],
                cities=['Алматы'],
                query_offset=-1,
                dry_run=True,
                providers=[provider],
            )
        self.assertEqual(provider.calls, [])
        self.assertEqual(SellerLead.objects.count(), before)


class ClassificationOrderTests(TestCase):
    def test_never_classified_leads_sort_before_retries(self):
        older = _lead(
            name='Older classified',
            business_type=SellerLead.BUSINESS_TYPE_UNKNOWN,
            last_classified_at=timezone.now() - timedelta(days=60),
        )
        newer = _lead(
            name='Newer classified',
            business_type=SellerLead.BUSINESS_TYPE_UNKNOWN,
            last_classified_at=timezone.now() - timedelta(days=31),
        )
        first_null = _lead(name='Never A')
        second_null = _lead(name='Never B')
        selected = select_leads_needing_classification(limit=10)
        self.assertIn('NULLS FIRST', str(selected.query))
        self.assertEqual(list(selected), [first_null, second_null, older, newer])
