from datetime import timedelta
import importlib

from django.apps import apps
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from catalog.models import Product, SellerProfile
from core.admin import (
    SellerLeadAdmin,
    confirm_card_a_is_duplicate_of_card_b,
    confirm_card_b_is_duplicate_of_card_a,
    find_seller_lead_duplicates,
    mark_seller_leads_lifecycle_rejected,
    mark_seller_leads_ready_to_invite,
)
from core.models import (
    Seller,
    SellerLead,
    SellerLeadContactCandidate,
    SellerLeadDuplicateMatch,
    SellerLeadEvidence,
    SellerLeadLocation,
    SellerLeadSource,
)
from core.services.seller_discovery_dedup import (
    find_possible_duplicates_for_leads,
    reject_seller_lead_duplicate,
    score_seller_lead_pair,
)
from core.services.seller_discovery_identity import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_DUPLICATE,
    LIFECYCLE_ENRICHED,
    LIFECYCLE_FOUND,
    LIFECYCLE_INVITED,
    LIFECYCLE_READY_TO_INVITE,
    LIFECYCLE_REJECTED,
    map_legacy_status_to_lifecycle,
    normalize_address,
    normalize_domain,
    normalize_instagram_identity,
    normalize_seller_name,
    normalize_seller_phone,
    refresh_seller_lead_identity,
)
from core.services.seller_discovery_sources import (
    SellerDiscoveryEvidenceError,
    add_seller_lead_evidence,
    choose_preferred_evidence,
    select_seller_lead_evidence,
    set_primary_location,
    upsert_seller_lead_source,
)

backfill_seller_leads = importlib.import_module(
    'core.migrations.0048_seller_discovery_foundation_data',
).forwards


class _Messages:
    def __init__(self):
        self.items = []

    def add(self, level, message, extra_tags=''):
        self.items.append(str(message))


def _lead(**kwargs):
    defaults = {'name': 'AutoChina Parts', 'city': 'Алматы'}
    defaults.update(kwargs)
    return SellerLead.objects.create(**defaults)


def _admin_request():
    user = get_user_model().objects.create_user(username='discovery-admin', password='test-pass-123')
    request = RequestFactory().post('/admin/core/sellerlead/')
    request.user = user
    request._messages = _Messages()
    return request


class SellerDiscoveryNormalizationTests(SimpleTestCase):
    def test_phone_formats_share_one_normalized_value(self):
        self.assertEqual(normalize_seller_phone('+7 701 123 45 67'), '77011234567')
        self.assertEqual(normalize_seller_phone('8 701 123 45 67'), '77011234567')
        self.assertEqual(normalize_seller_phone('87011234567'), '77011234567')
        self.assertEqual(normalize_seller_phone('77011234567'), '77011234567')

    def test_domain_strips_host_noise(self):
        expected = 'example.kz'
        self.assertEqual(normalize_domain('https://www.Example.kz/path?q=1'), expected)
        self.assertEqual(normalize_domain('example.kz'), expected)
        self.assertEqual(normalize_domain('http://example.kz/'), expected)
        self.assertEqual(normalize_domain('example.kz.'), expected)

    def test_instagram_identity(self):
        expected = 'example.shop'
        self.assertEqual(normalize_instagram_identity('@example.shop'), expected)
        self.assertEqual(normalize_instagram_identity('@Example.Shop'), expected)
        self.assertEqual(
            normalize_instagram_identity('https://instagram.com/example.shop/'),
            expected,
        )
        self.assertEqual(
            normalize_instagram_identity('https://www.instagram.com/example.shop'),
            expected,
        )

    def test_name_and_address_keep_meaningful_words(self):
        self.assertEqual(normalize_seller_name('  AutoChina   Parts '), 'autochina parts')
        self.assertEqual(normalize_seller_name('«AutoChina» Parts'), 'autochina parts')
        self.assertIn('магазин', normalize_seller_name('Магазин AutoChina'))
        self.assertEqual(normalize_address('  ул. Абая,  10 '), 'ул абая 10')


class SellerDiscoveryLifecycleMappingTests(SimpleTestCase):
    def test_known_legacy_statuses(self):
        self.assertEqual(
            map_legacy_status_to_lifecycle('needs_review', enriched=False),
            LIFECYCLE_FOUND,
        )
        self.assertEqual(
            map_legacy_status_to_lifecycle('new', enriched=True),
            LIFECYCLE_ENRICHED,
        )
        self.assertEqual(
            map_legacy_status_to_lifecycle('verified', enriched=False),
            LIFECYCLE_READY_TO_INVITE,
        )
        self.assertEqual(
            map_legacy_status_to_lifecycle('contacted', enriched=False),
            LIFECYCLE_INVITED,
        )
        self.assertEqual(
            map_legacy_status_to_lifecycle('registered', enriched=False),
            LIFECYCLE_ACTIVE,
        )
        self.assertEqual(
            map_legacy_status_to_lifecycle('duplicate', enriched=False),
            LIFECYCLE_DUPLICATE,
        )
        self.assertEqual(
            map_legacy_status_to_lifecycle('rejected', enriched=False),
            LIFECYCLE_REJECTED,
        )
        self.assertEqual(
            map_legacy_status_to_lifecycle('not_seller', enriched=False),
            LIFECYCLE_REJECTED,
        )

    def test_unknown_legacy_status_stays_found(self):
        self.assertEqual(map_legacy_status_to_lifecycle('replied', enriched=True), LIFECYCLE_FOUND)
        self.assertEqual(map_legacy_status_to_lifecycle('no_whatsapp', enriched=False), LIFECYCLE_FOUND)
        self.assertEqual(map_legacy_status_to_lifecycle('interested', enriched=True), LIFECYCLE_FOUND)

    def test_model_constants_match_service(self):
        self.assertEqual(SellerLead.LIFECYCLE_FOUND, LIFECYCLE_FOUND)
        self.assertEqual(SellerLead.LIFECYCLE_READY_TO_INVITE, LIFECYCLE_READY_TO_INVITE)
        self.assertEqual(SellerLead.LIFECYCLE_DUPLICATE, LIFECYCLE_DUPLICATE)


class SellerLeadSourceTests(TestCase):
    def test_upsert_is_idempotent_and_refreshes_last_seen(self):
        lead = _lead()
        first_seen = timezone.now() - timedelta(days=2)
        later = timezone.now()
        first = upsert_seller_lead_source(
            lead,
            source_type=SellerLeadSource.SOURCE_WEB_SEARCH,
            provider='',
            source_url='https://example.kz/shop',
            display_name='AutoChina',
            observed_at=first_seen,
        )
        second = upsert_seller_lead_source(
            lead,
            source_type=SellerLeadSource.SOURCE_WEB_SEARCH,
            source_url='https://example.kz/shop',
            observed_at=later,
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SellerLeadSource.objects.filter(seller_lead=lead).count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.first_seen_at, first_seen)
        self.assertEqual(second.last_seen_at, later)

    def test_different_sources_are_kept(self):
        lead = _lead()
        upsert_seller_lead_source(
            lead,
            source_type=SellerLeadSource.SOURCE_INSTAGRAM,
            source_url='https://instagram.com/example.shop/',
            observed_at=timezone.now(),
        )
        upsert_seller_lead_source(
            lead,
            source_type=SellerLeadSource.SOURCE_WEBSITE,
            source_url='https://example.kz/',
            observed_at=timezone.now(),
        )
        upsert_seller_lead_source(
            lead,
            source_type=SellerLeadSource.SOURCE_GOOGLE_PLACES,
            provider='google',
            external_id='places-1',
            source_url='https://maps.google.com/?q=autochina',
            observed_at=timezone.now(),
        )
        self.assertEqual(lead.sources.count(), 3)


class SellerLeadEvidenceTests(TestCase):
    def test_multiple_evidence_rows_and_single_selected_value(self):
        lead = _lead()
        first = add_seller_lead_evidence(
            lead,
            field_name='phone',
            value='+7 701 123 45 67',
            extraction_method=SellerLeadEvidence.METHOD_SEARCH_RESULT,
            is_selected=True,
        )
        second = add_seller_lead_evidence(
            lead,
            field_name='phone',
            value='+7 701 000 00 02',
            extraction_method=SellerLeadEvidence.METHOD_MANUAL,
        )
        self.assertEqual(lead.evidences.filter(field_name='phone').count(), 2)
        select_seller_lead_evidence(second)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_selected)
        self.assertTrue(second.is_selected)

    def test_owner_verified_flag_persists_and_blocks_external_override(self):
        lead = _lead()
        owner = add_seller_lead_evidence(
            lead,
            field_name='phone',
            value='+7 701 123 45 67',
            extraction_method=SellerLeadEvidence.METHOD_SELLER,
            is_owner_verified=True,
            is_selected=True,
        )
        external = add_seller_lead_evidence(
            lead,
            field_name='phone',
            value='87019998877',
            extraction_method=SellerLeadEvidence.METHOD_SEARCH_RESULT,
            is_selected=True,
        )
        owner.refresh_from_db()
        external.refresh_from_db()
        self.assertTrue(owner.is_owner_verified)
        self.assertTrue(owner.is_selected)
        self.assertFalse(external.is_selected)
        with self.assertRaises(SellerDiscoveryEvidenceError):
            select_seller_lead_evidence(external)
        owner.refresh_from_db()
        self.assertTrue(owner.is_selected)
        self.assertEqual(choose_preferred_evidence(lead, 'phone').pk, owner.pk)

    def test_repeated_external_observation_does_not_clear_owner_flag(self):
        lead = _lead()
        owner = add_seller_lead_evidence(
            lead,
            field_name='website',
            value='https://www.Example.kz/path',
            extraction_method=SellerLeadEvidence.METHOD_SELLER,
            is_owner_verified=True,
            is_selected=True,
        )
        again = add_seller_lead_evidence(
            lead,
            field_name='website',
            value='https://example.kz/other',
            extraction_method=SellerLeadEvidence.METHOD_SELLER,
            is_owner_verified=False,
            observed_at=timezone.now() + timedelta(hours=1),
        )
        self.assertEqual(owner.pk, again.pk)
        again.refresh_from_db()
        self.assertTrue(again.is_owner_verified)
        self.assertTrue(again.is_selected)

    def test_owner_verified_address_stays_selected_when_stronger_external_arrives(self):
        lead = _lead()
        owner = add_seller_lead_evidence(
            lead,
            field_name='address',
            value='адрес продавца',
            extraction_method=SellerLeadEvidence.METHOD_SELLER,
            is_owner_verified=True,
            is_selected=True,
            confidence=10,
        )
        external = add_seller_lead_evidence(
            lead,
            field_name='address',
            value='другой внешний адрес',
            extraction_method=SellerLeadEvidence.METHOD_SEARCH_RESULT,
            confidence=100,
            is_selected=True,
        )
        owner.refresh_from_db()
        external.refresh_from_db()
        self.assertNotEqual(owner.pk, external.pk)
        self.assertTrue(owner.is_selected)
        self.assertTrue(owner.is_owner_verified)
        self.assertFalse(external.is_selected)
        self.assertEqual(external.confidence, 100)
        self.assertEqual(choose_preferred_evidence(lead, 'address').pk, owner.pk)

    def test_two_selected_values_for_one_field_are_rejected_by_the_database(self):
        lead = _lead()
        now = timezone.now()
        SellerLeadEvidence.objects.create(
            seller_lead=lead,
            field_name='city',
            value='Алматы',
            observed_at=now,
            is_selected=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SellerLeadEvidence.objects.create(
                    seller_lead=lead,
                    field_name='city',
                    value='Астана',
                    observed_at=now,
                    is_selected=True,
                )
        self.assertEqual(
            lead.evidences.filter(field_name='city', is_selected=True).count(),
            1,
        )


class SellerLeadLocationTests(TestCase):
    def test_multiple_locations_and_one_primary(self):
        lead = _lead()
        now = timezone.now()
        first = SellerLeadLocation.objects.create(
            seller_lead=lead,
            city='Алматы',
            address='ул. Абая, 1',
            first_seen_at=now,
            last_seen_at=now,
            is_primary=True,
        )
        second = SellerLeadLocation.objects.create(
            seller_lead=lead,
            city='Астана',
            address='пр. Республики, 2',
            first_seen_at=now,
            last_seen_at=now,
        )
        self.assertEqual(lead.locations.count(), 2)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SellerLeadLocation.objects.create(
                    seller_lead=lead,
                    city='Шымкент',
                    address='ул. Тауке хана, 3',
                    first_seen_at=now,
                    last_seen_at=now,
                    is_primary=True,
                )
        chosen = set_primary_location(second)
        first.refresh_from_db()
        lead.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertTrue(chosen.is_primary)
        self.assertEqual(lead.normalized_address, normalize_address('пр. Республики, 2'))


class SellerDiscoveryRangeTests(TestCase):
    def _assert_bounds(self, instance, field_name):
        for allowed in (0, 100):
            setattr(instance, field_name, allowed)
            instance.full_clean()
            instance.save()
        setattr(instance, field_name, 101)
        with self.assertRaises(ValidationError):
            instance.full_clean()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                instance.save()
        instance.refresh_from_db()
        self.assertEqual(getattr(instance, field_name), 100)

    def test_confidence_and_score_are_limited_to_0_through_100(self):
        now = timezone.now()
        lead = _lead(overall_confidence=0)
        self._assert_bounds(lead, 'overall_confidence')

        source = SellerLeadSource.objects.create(
            seller_lead=lead,
            source_type=SellerLeadSource.SOURCE_MANUAL,
            first_seen_at=now,
            last_seen_at=now,
            source_confidence=0,
        )
        self._assert_bounds(source, 'source_confidence')

        evidence = SellerLeadEvidence.objects.create(
            seller_lead=lead,
            field_name='name',
            value='AutoChina',
            observed_at=now,
            confidence=0,
        )
        self._assert_bounds(evidence, 'confidence')

        location = SellerLeadLocation.objects.create(
            seller_lead=lead,
            city='Алматы',
            address='ул. Абая, 1',
            first_seen_at=now,
            last_seen_at=now,
            confidence=0,
        )
        self._assert_bounds(location, 'confidence')

        other = _lead(name='Second card')
        match = SellerLeadDuplicateMatch.objects.create(
            lead_a=lead,
            lead_b=other,
            score=0,
        )
        self._assert_bounds(match, 'score')


class SellerLeadDuplicateTests(TestCase):
    def test_same_phone_is_possible_duplicate_with_high_score(self):
        left = _lead(name='Shop A', whatsapp='77011234567')
        right = _lead(name='Shop B', city='Астана')
        SellerLeadContactCandidate.objects.create(
            seller_lead=right,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='8 701 123 45 67',
            confidence='high',
        )
        matches = find_possible_duplicates_for_leads([left])
        self.assertEqual(len(matches), 1)
        match = SellerLeadDuplicateMatch.objects.get()
        self.assertGreaterEqual(match.score, 90)
        self.assertEqual(match.status, SellerLeadDuplicateMatch.STATUS_POSSIBLE)
        self.assertIn('phone', match.reasons)
        left.refresh_from_db()
        right.refresh_from_db()
        self.assertEqual(left.lifecycle_status, SellerLead.LIFECYCLE_POSSIBLE_DUPLICATE)
        self.assertIsNone(left.duplicate_of_id)
        self.assertIsNone(right.duplicate_of_id)

    def test_same_instagram_is_high_score(self):
        left = _lead(name='Shop A', instagram_username='Example.Shop')
        right = _lead(name='Shop B', instagram_username='example.shop', city='Астана')
        matches = find_possible_duplicates_for_leads([left, right])
        self.assertEqual(len(matches), 1)
        self.assertGreaterEqual(matches[0].score, 95)
        self.assertEqual(matches[0].status, SellerLeadDuplicateMatch.STATUS_POSSIBLE)
        self.assertIn('instagram', matches[0].reasons)

    def test_same_domain_is_strong_possible_match(self):
        left = _lead(website_url='https://www.Example.kz/path?q=1')
        right = _lead(name='Other Parts', website_url='http://example.kz/', city='Астана')
        matches = find_possible_duplicates_for_leads([right, left])
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertGreaterEqual(match.score, 80)
        self.assertLess(match.lead_a_id, match.lead_b_id)
        self.assertEqual(match.status, SellerLeadDuplicateMatch.STATUS_POSSIBLE)
        self.assertTrue(match.lead_a_id)
        left.refresh_from_db()
        right.refresh_from_db()
        self.assertEqual(left.lifecycle_status, SellerLead.LIFECYCLE_POSSIBLE_DUPLICATE)
        self.assertIsNone(left.duplicate_of_id)
        self.assertEqual(SellerLead.objects.count(), 2)

    def test_similar_name_is_not_confirmed(self):
        left = _lead(name='AutoChina Parts')
        right = _lead(name='AutoChina Partz', city='Астана')
        refresh_seller_lead_identity(left)
        refresh_seller_lead_identity(right)
        scored = score_seller_lead_pair(left, right)
        self.assertIsNotNone(scored)
        self.assertFalse(scored.strong)
        self.assertLess(scored.score, 50)
        find_possible_duplicates_for_leads([left, right])
        self.assertFalse(SellerLeadDuplicateMatch.objects.exists())
        left.refresh_from_db()
        right.refresh_from_db()
        self.assertIsNone(left.duplicate_of_id)
        self.assertNotEqual(left.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertNotEqual(right.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)

    def test_same_name_different_city_is_not_confirmed(self):
        left = _lead(name='AutoChina Parts', city='Алматы')
        right = _lead(name='AutoChina Parts', city='Астана')
        find_possible_duplicates_for_leads([left, right])
        self.assertFalse(
            SellerLeadDuplicateMatch.objects.filter(
                status=SellerLeadDuplicateMatch.STATUS_CONFIRMED,
            ).exists(),
        )
        left.refresh_from_db()
        right.refresh_from_db()
        self.assertIsNone(left.duplicate_of_id)
        self.assertIsNone(right.duplicate_of_id)
        self.assertEqual(left.lifecycle_status, SellerLead.LIFECYCLE_FOUND)
        self.assertEqual(right.lifecycle_status, SellerLead.LIFECYCLE_FOUND)

    def test_lead_is_not_compared_with_itself(self):
        lead = _lead(whatsapp='77011234567', website_url='https://example.kz')
        self.assertIsNone(score_seller_lead_pair(lead, lead))
        self.assertEqual(find_possible_duplicates_for_leads([lead]), [])
        self.assertFalse(SellerLeadDuplicateMatch.objects.exists())

    def test_reverse_pair_is_stored_once(self):
        left = _lead(website_url='https://example.kz/a')
        right = _lead(name='Second', website_url='http://www.example.kz/b', city='Астана')
        find_possible_duplicates_for_leads([right])
        find_possible_duplicates_for_leads([left, right])
        self.assertEqual(SellerLeadDuplicateMatch.objects.count(), 1)
        match = SellerLeadDuplicateMatch.objects.get()
        self.assertLess(match.lead_a_id, match.lead_b_id)

    def test_address_match_does_not_confirm_or_flip_lifecycle_without_strong_signal(self):
        left = _lead(name='Alpha Parts')
        right = _lead(name='Beta Service', city='Астана')
        SellerLead.objects.filter(pk=left.pk).update(normalized_address='абая 10')
        SellerLead.objects.filter(pk=right.pk).update(normalized_address='абая 10')
        left.refresh_from_db()
        right.refresh_from_db()
        matches = find_possible_duplicates_for_leads([left, right])
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].status, SellerLeadDuplicateMatch.STATUS_POSSIBLE)
        self.assertNotIn('phone', matches[0].reasons)
        left.refresh_from_db()
        right.refresh_from_db()
        self.assertEqual(left.lifecycle_status, SellerLead.LIFECYCLE_FOUND)
        self.assertIsNone(left.duplicate_of_id)

    def test_database_rejects_self_pair_and_both_directions(self):
        lead = _lead(name='Self card')
        other = _lead(name='Other card')
        self_pair = SellerLeadDuplicateMatch(lead_a=lead, lead_b=lead, score=10)
        with self.assertRaises(ValidationError):
            self_pair.full_clean()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SellerLeadDuplicateMatch.objects.create(lead_a=lead, lead_b=lead, score=10)

        stored = SellerLeadDuplicateMatch.objects.create(lead_a=other, lead_b=lead, score=90)
        stored.refresh_from_db()
        self.assertLess(stored.lead_a_id, stored.lead_b_id)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SellerLeadDuplicateMatch.objects.create(lead_a=lead, lead_b=other, score=90)
        self.assertEqual(SellerLeadDuplicateMatch.objects.count(), 1)

    def test_find_duplicates_does_not_rewrite_mature_lifecycle(self):
        protected = (
            SellerLead.LIFECYCLE_INVITED,
            SellerLead.LIFECYCLE_CLAIMED,
            SellerLead.LIFECYCLE_VERIFIED,
            SellerLead.LIFECYCLE_ACTIVE,
            SellerLead.LIFECYCLE_REJECTED,
            SellerLead.LIFECYCLE_CLOSED,
        )
        for lifecycle in protected:
            mature = _lead(
                name=f'Mature {lifecycle}',
                website_url=f'https://{lifecycle}.example.kz',
                lifecycle_status=lifecycle,
            )
            fresh = _lead(
                name=f'Fresh {lifecycle}',
                website_url=f'http://www.{lifecycle}.example.kz/catalog',
                city='Астана',
            )
            matches = find_possible_duplicates_for_leads([mature, fresh])
            mature.refresh_from_db()
            fresh.refresh_from_db()
            self.assertEqual(len(matches), 1)
            self.assertEqual(mature.lifecycle_status, lifecycle)
            self.assertIsNone(mature.duplicate_of_id)
            self.assertEqual(fresh.lifecycle_status, SellerLead.LIFECYCLE_POSSIBLE_DUPLICATE)


class SellerDiscoveryAdminWorkflowTests(TestCase):
    def test_find_and_confirm_duplicate_keeps_both_leads(self):
        older = _lead(name='Canonical Shop', website_url='https://shop.example.kz')
        newer = _lead(name='Duplicate Shop', website_url='http://www.shop.example.kz/contacts', city='Астана')
        older.collected_at = timezone.now() - timedelta(days=3)
        older.save(update_fields=['collected_at', 'updated_at'])
        seller_count = Seller.objects.count()
        request = _admin_request()
        admin = SellerLeadAdmin(SellerLead, AdminSite())
        find_seller_lead_duplicates(
            admin,
            request,
            SellerLead.objects.filter(pk__in=[older.pk, newer.pk]),
        )
        match = SellerLeadDuplicateMatch.objects.get()
        confirm_card_b_is_duplicate_of_card_a(
            None,
            request,
            SellerLeadDuplicateMatch.objects.filter(pk=match.pk),
        )
        older.refresh_from_db()
        newer.refresh_from_db()
        match.refresh_from_db()
        self.assertEqual(match.status, SellerLeadDuplicateMatch.STATUS_CONFIRMED)
        self.assertEqual(match.resolved_by_id, request.user.pk)
        self.assertIsNotNone(match.resolved_at)
        self.assertEqual(newer.duplicate_of_id, older.pk)
        self.assertEqual(newer.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertIsNone(older.duplicate_of_id)
        self.assertNotEqual(older.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertEqual(SellerLead.objects.filter(pk__in=[older.pk, newer.pk]).count(), 2)
        self.assertEqual(Seller.objects.count(), seller_count)
        self.assertGreaterEqual(len(admin.get_queryset(request)), 2)

    def test_confirm_both_directions_are_explicit(self):
        from core.services.seller_discovery_dedup import confirm_seller_lead_duplicate

        first_a = _lead(name='Card A1', website_url='https://dir-a.example.kz')
        first_b = _lead(name='Card B1', website_url='http://www.dir-a.example.kz', city='Астана')
        second_a = _lead(name='Card A2', website_url='https://dir-b.example.kz')
        second_b = _lead(name='Card B2', website_url='http://dir-b.example.kz/path', city='Шымкент')
        find_possible_duplicates_for_leads([first_a, first_b, second_a, second_b])
        request = _admin_request()
        match_first = SellerLeadDuplicateMatch.objects.get(
            lead_a_id=min(first_a.pk, first_b.pk),
            lead_b_id=max(first_a.pk, first_b.pk),
        )
        match_second = SellerLeadDuplicateMatch.objects.get(
            lead_a_id=min(second_a.pk, second_b.pk),
            lead_b_id=max(second_a.pk, second_b.pk),
        )
        confirm_card_a_is_duplicate_of_card_b(
            None,
            request,
            SellerLeadDuplicateMatch.objects.filter(pk=match_first.pk),
        )
        confirm_card_b_is_duplicate_of_card_a(
            None,
            request,
            SellerLeadDuplicateMatch.objects.filter(pk=match_second.pk),
        )
        match_first.refresh_from_db()
        match_second.refresh_from_db()
        match_first.lead_a.refresh_from_db()
        match_first.lead_b.refresh_from_db()
        match_second.lead_a.refresh_from_db()
        match_second.lead_b.refresh_from_db()
        self.assertEqual(match_first.lead_a.duplicate_of_id, match_first.lead_b_id)
        self.assertEqual(match_first.lead_a.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertIsNone(match_first.lead_b.duplicate_of_id)
        self.assertNotEqual(match_first.lead_b.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertEqual(match_second.lead_b.duplicate_of_id, match_second.lead_a_id)
        self.assertEqual(match_second.lead_b.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertIsNone(match_second.lead_a.duplicate_of_id)
        self.assertEqual(match_first.status, SellerLeadDuplicateMatch.STATUS_CONFIRMED)
        self.assertEqual(match_first.resolved_by_id, request.user.pk)
        self.assertIsNotNone(match_first.resolved_at)
        self.assertEqual(SellerLead.objects.filter(
            pk__in=[first_a.pk, first_b.pk, second_a.pk, second_b.pk],
        ).count(), 4)
        with self.assertRaises(TypeError):
            confirm_seller_lead_duplicate(match_first, resolved_by=request.user)

    def test_reject_duplicate_does_not_rewrite_lifecycle(self):
        left = _lead(website_url='https://reject.example.kz')
        right = _lead(name='Other', website_url='http://reject.example.kz', city='Астана')
        find_possible_duplicates_for_leads([left, right])
        left.refresh_from_db()
        lifecycle_before = left.lifecycle_status
        match = SellerLeadDuplicateMatch.objects.get()
        reject_seller_lead_duplicate(match, resolved_by=None)
        match.refresh_from_db()
        left.refresh_from_db()
        self.assertEqual(match.status, SellerLeadDuplicateMatch.STATUS_REJECTED)
        self.assertEqual(left.lifecycle_status, lifecycle_before)
        self.assertIsNone(left.duplicate_of_id)

    def test_ready_and_rejected_actions_touch_only_lifecycle(self):
        lead = _lead(status=SellerLead.STATUS_NEEDS_REVIEW)
        request = _admin_request()
        mark_seller_leads_ready_to_invite(None, request, SellerLead.objects.filter(pk=lead.pk))
        lead.refresh_from_db()
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_READY_TO_INVITE)
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)
        mark_seller_leads_lifecycle_rejected(None, request, SellerLead.objects.filter(pk=lead.pk))
        lead.refresh_from_db()
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_REJECTED)
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)


class SellerDiscoveryLegacyCompatibilityTests(TestCase):
    def test_new_lead_keeps_legacy_status_and_defaults_lifecycle_to_found(self):
        lead = _lead(status=SellerLead.STATUS_NEEDS_REVIEW, source_type='web_search')
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_FOUND)

    def test_refresh_identity_from_canonical_fields(self):
        lead = _lead(
            name='  AutoChina   Parts ',
            whatsapp='8 701 123 45 67',
            website_url='https://www.Example.kz/path?q=1',
            instagram_username='@Example.Shop',
        )
        refresh_seller_lead_identity(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.normalized_phone, '77011234567')
        self.assertEqual(lead.normalized_domain, 'example.kz')
        self.assertEqual(lead.normalized_instagram, 'example.shop')
        self.assertEqual(lead.normalized_name, 'autochina parts')

    def test_primary_contact_approval_refreshes_normalized_phone(self):
        lead = _lead()
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            value='87011234567',
            confidence='high',
        )
        candidate.approve_as_primary()
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(lead.normalized_phone, '77011234567')

    def test_data_migration_backfill_is_conservative(self):
        verified = _lead(
            name='Old Shop',
            status=SellerLead.STATUS_VERIFIED,
            whatsapp='77019876543',
            website_url='https://www.Old-Shop.kz/catalog',
            instagram_username='Old.Shop',
            instagram_url='https://instagram.com/old.shop/',
            source_type='web_search',
            source_url='https://www.instagram.com/old.shop/',
            checked_at=timezone.now(),
        )
        replied = _lead(
            name='Replied Shop',
            status=SellerLead.STATUS_REPLIED,
            source_type='manual',
        )
        SellerLead.objects.filter(pk__in=[verified.pk, replied.pk]).update(
            lifecycle_status=SellerLead.LIFECYCLE_FOUND,
            normalized_name='',
            normalized_phone='',
            normalized_domain='',
            normalized_instagram='',
            last_seen_at=None,
        )
        backfill_seller_leads(apps, None)
        verified.refresh_from_db()
        replied.refresh_from_db()
        self.assertEqual(verified.lifecycle_status, SellerLead.LIFECYCLE_READY_TO_INVITE)
        self.assertEqual(verified.normalized_phone, '77019876543')
        self.assertEqual(verified.normalized_domain, 'old-shop.kz')
        self.assertEqual(verified.normalized_instagram, 'old.shop')
        self.assertIsNotNone(verified.last_seen_at)
        source = verified.sources.get(source_type=SellerLeadSource.SOURCE_WEB_SEARCH)
        self.assertEqual(source.source_type, SellerLeadSource.SOURCE_WEB_SEARCH)
        self.assertEqual(source.provider, '')
        self.assertEqual(source.external_id, '')
        self.assertTrue(verified.evidences.filter(field_name='phone', normalized_value='77019876543').exists())
        self.assertEqual(replied.lifecycle_status, SellerLead.LIFECYCLE_FOUND)
        self.assertTrue(replied.sources.filter(source_type=SellerLeadSource.SOURCE_MANUAL).exists())
        self.assertEqual(verified.status, SellerLead.STATUS_VERIFIED)
        self.assertEqual(replied.status, SellerLead.STATUS_REPLIED)
        self.assertIsNone(verified.duplicate_of_id)
        self.assertIsNone(replied.duplicate_of_id)
        source_count = verified.sources.count()
        evidence_count = verified.evidences.count()
        account_counts = (
            Seller.objects.count(),
            SellerProfile.objects.count(),
            get_user_model().objects.count(),
            Product.objects.count(),
        )
        backfill_seller_leads(apps, None)
        verified.refresh_from_db()
        replied.refresh_from_db()
        self.assertEqual(verified.sources.count(), source_count)
        self.assertEqual(verified.evidences.count(), evidence_count)
        self.assertEqual(verified.status, SellerLead.STATUS_VERIFIED)
        self.assertEqual(replied.status, SellerLead.STATUS_REPLIED)
        self.assertIsNone(verified.duplicate_of_id)
        self.assertEqual(
            (
                Seller.objects.count(),
                SellerProfile.objects.count(),
                get_user_model().objects.count(),
                Product.objects.count(),
            ),
            account_counts,
        )


class SellerDiscoveryConversionProtectionTests(TestCase):
    def test_discovery_services_do_not_create_accounts_or_products(self):
        resolver = get_user_model().objects.create_user(
            username='discovery-resolver',
            password='test-pass-123',
        )
        before = (
            Seller.objects.count(),
            SellerProfile.objects.count(),
            get_user_model().objects.count(),
            Product.objects.count(),
        )
        lead = _lead(name='AutoChina Parts', website_url='https://autochina.kz')
        other = _lead(name='AutoChina Parts 2', website_url='https://www.autochina.kz/catalog', city='Астана')
        upsert_seller_lead_source(
            lead,
            source_type=SellerLeadSource.SOURCE_WEBSITE,
            source_url='https://autochina.kz',
            observed_at=timezone.now(),
        )
        add_seller_lead_evidence(
            lead,
            field_name='vehicle_brand',
            value='Chery',
            extraction_method=SellerLeadEvidence.METHOD_PARSER,
        )
        now = timezone.now()
        location = SellerLeadLocation.objects.create(
            seller_lead=lead,
            city='Алматы',
            address='ул. Абая, 10',
            first_seen_at=now,
            last_seen_at=now,
        )
        set_primary_location(location)
        matches = find_possible_duplicates_for_leads([lead, other])
        from core.services.seller_discovery_dedup import confirm_seller_lead_duplicate

        confirm_seller_lead_duplicate(
            matches[0],
            canonical_lead=matches[0].lead_a,
            resolved_by=resolver,
        )
        lead.refresh_from_db()
        other.refresh_from_db()
        matches[0].refresh_from_db()
        canonical_id = matches[0].lead_a_id
        duplicate = lead if lead.pk != canonical_id else other
        canonical = lead if lead.pk == canonical_id else other
        self.assertIsNone(canonical.duplicate_of_id)
        self.assertNotEqual(canonical.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertEqual(duplicate.duplicate_of_id, canonical.pk)
        self.assertEqual(duplicate.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertEqual(matches[0].status, SellerLeadDuplicateMatch.STATUS_CONFIRMED)
        self.assertEqual(matches[0].resolved_by_id, resolver.pk)
        self.assertIsNotNone(matches[0].resolved_at)
        self.assertEqual(
            (
                Seller.objects.count(),
                SellerProfile.objects.count(),
                get_user_model().objects.count(),
                Product.objects.count(),
            ),
            before,
        )
        self.assertEqual(SellerLead.objects.count(), 2)
