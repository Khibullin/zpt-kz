from __future__ import annotations

import io
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import (
    BUYER_CITY_INTEREST_REQUEST_CITY,
    BUYER_CITY_INTEREST_SELECTED_CITY,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_PURPOSE_SERVICE,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerCategoryInterest,
    BuyerCityInterest,
    BuyerContact,
    BuyerPortalAccess,
    BuyerVehicle,
    ContactConsent,
    Request,
)
from core.services.buyer_contact_service import (
    SYNC_STATUS_REQUEST_LINK_CONFLICT,
    SYNC_STATUS_SKIPPED_INVALID_PHONE,
    SYNC_STATUS_SKIPPED_UNSAVED_REQUEST,
    SYNC_STATUS_SYNCED,
    parse_selected_cities,
    rebuild_buyer_contact,
    sync_buyer_contact_from_request,
)


def create_request(**kwargs) -> Request:
    defaults = {
        'transport_type': 'car',
        'phone': '77011234567',
        'brand': 'Toyota',
        'model': 'Camry',
        'category': 'Тормоза',
        'city': 'Алматы',
        'search_scope': 'city',
    }
    defaults.update(kwargs)
    return Request.objects.create(**defaults)


class SyncBuyerContactFromRequestTests(TestCase):
    def test_creates_buyer_from_valid_request(self):
        req = create_request()
        result = sync_buyer_contact_from_request(req)

        self.assertEqual(result.status, SYNC_STATUS_SYNCED)
        self.assertTrue(result.buyer_created)
        self.assertTrue(BuyerContact.objects.filter(phone_normalized='77011234567').exists())

    def test_links_request_to_buyer(self):
        req = create_request()
        sync_buyer_contact_from_request(req)
        req.refresh_from_db()

        self.assertIsNotNone(req.buyer_contact_id)
        self.assertEqual(req.buyer_contact.phone_normalized, '77011234567')

    def test_eight_prefix_converted(self):
        req = create_request(phone='87011234567')
        sync_buyer_contact_from_request(req)
        req.refresh_from_db()

        self.assertEqual(req.buyer_contact.phone_normalized, '77011234567')

    def test_eight_and_seven_share_one_buyer(self):
        req7 = create_request(phone='77012223344')
        req8 = create_request(phone='87012223344', brand='Honda', model='Civic')
        sync_buyer_contact_from_request(req7)
        sync_buyer_contact_from_request(req8)

        self.assertEqual(BuyerContact.objects.filter(phone_normalized='77012223344').count(), 1)
        req8.refresh_from_db()
        self.assertEqual(req8.buyer_contact_id, req7.buyer_contact_id)

    def test_invalid_phone_skipped(self):
        req = create_request(phone='invalid')
        result = sync_buyer_contact_from_request(req)

        self.assertEqual(result.status, SYNC_STATUS_SKIPPED_INVALID_PHONE)
        req.refresh_from_db()
        self.assertIsNone(req.buyer_contact_id)
        self.assertEqual(BuyerContact.objects.count(), 0)

    def test_unsaved_request_skipped(self):
        req = Request(transport_type='car', phone='77011234567')
        result = sync_buyer_contact_from_request(req)

        self.assertEqual(result.status, SYNC_STATUS_SKIPPED_UNSAVED_REQUEST)

    def test_repeat_sync_does_not_increase_counters(self):
        req = create_request()
        sync_buyer_contact_from_request(req)
        buyer = req.buyer_contact
        buyer.refresh_from_db()
        first_count = buyer.requests_count

        sync_buyer_contact_from_request(req)
        buyer.refresh_from_db()
        self.assertEqual(buyer.requests_count, first_count)

    def test_repeat_sync_does_not_duplicate_vehicles(self):
        req = create_request()
        sync_buyer_contact_from_request(req)
        sync_buyer_contact_from_request(req)
        buyer = req.buyer_contact

        self.assertEqual(buyer.vehicles.count(), 1)

    def test_repeat_sync_does_not_duplicate_categories(self):
        req = create_request()
        sync_buyer_contact_from_request(req)
        sync_buyer_contact_from_request(req)
        buyer = req.buyer_contact

        self.assertEqual(buyer.category_interests.count(), 1)

    def test_repeat_sync_does_not_duplicate_cities(self):
        req = create_request()
        sync_buyer_contact_from_request(req)
        sync_buyer_contact_from_request(req)
        buyer = req.buyer_contact

        self.assertEqual(buyer.city_interests.count(), 1)

    def test_repeat_sync_does_not_duplicate_consents(self):
        req = create_request()
        sync_buyer_contact_from_request(req)
        sync_buyer_contact_from_request(req)
        buyer = req.buyer_contact

        self.assertEqual(buyer.consents.count(), 3)


class MultipleRequestsSyncTests(TestCase):
    def test_two_requests_same_phone_have_requests_count_two(self):
        req1 = create_request(phone='77013334455')
        req2 = create_request(phone='77013334455', brand='Honda', model='Civic')
        sync_buyer_contact_from_request(req1)
        sync_buyer_contact_from_request(req2)

        buyer = req1.buyer_contact
        buyer.refresh_from_db()
        self.assertEqual(buyer.requests_count, 2)

    def test_first_and_last_request_dates(self):
        base = timezone.now() - timedelta(days=2)
        req1 = create_request(phone='77014445566')
        Request.objects.filter(pk=req1.pk).update(created_at=base)
        req1.refresh_from_db()

        req2 = create_request(phone='77014445566', brand='Honda', model='Civic')
        sync_buyer_contact_from_request(req1)
        sync_buyer_contact_from_request(req2)

        buyer = req1.buyer_contact
        buyer.refresh_from_db()
        self.assertEqual(buyer.first_request_at, req1.created_at)
        self.assertEqual(buyer.last_request_at, req2.created_at)

    def test_last_search_scope_from_latest_request(self):
        req1 = create_request(phone='77015556677', search_scope='city')
        req2 = create_request(
            phone='77015556677',
            search_scope='custom',
            selected_cities='Астана',
        )
        sync_buyer_contact_from_request(req1)
        sync_buyer_contact_from_request(req2)

        buyer = req1.buyer_contact
        buyer.refresh_from_db()
        self.assertEqual(buyer.last_search_scope, 'custom')

    def test_scope_counters(self):
        req1 = create_request(phone='77016667788', search_scope='city')
        req2 = create_request(phone='77016667788', search_scope='kazakhstan')
        req3 = create_request(
            phone='77016667788',
            search_scope='custom',
            selected_cities='Астана',
        )
        for req in (req1, req2, req3):
            sync_buyer_contact_from_request(req)

        buyer = req1.buyer_contact
        buyer.refresh_from_db()
        self.assertEqual(buyer.city_scope_requests_count, 1)
        self.assertEqual(buyer.kazakhstan_scope_requests_count, 1)
        self.assertEqual(buyer.custom_scope_requests_count, 1)

    def test_different_vehicles_create_separate_records(self):
        req1 = create_request(phone='77017778899', brand='Toyota', model='Camry')
        req2 = create_request(phone='77017778899', brand='Honda', model='Civic')
        sync_buyer_contact_from_request(req1)
        sync_buyer_contact_from_request(req2)

        buyer = req1.buyer_contact
        self.assertEqual(buyer.vehicles.count(), 2)

    def test_same_vehicle_different_case_merged(self):
        req1 = create_request(phone='77018889900', brand='Toyota', model='Camry')
        req2 = create_request(phone='77018889900', brand='toyota', model='camry')
        sync_buyer_contact_from_request(req1)
        sync_buyer_contact_from_request(req2)

        buyer = req1.buyer_contact
        self.assertEqual(buyer.vehicles.count(), 1)
        vehicle = buyer.vehicles.get()
        self.assertEqual(vehicle.requests_count, 2)

    def test_same_category_different_case_merged(self):
        req1 = create_request(phone='77019990011', category='Тормоза')
        req2 = create_request(phone='77019990011', category='тормоза')
        sync_buyer_contact_from_request(req1)
        sync_buyer_contact_from_request(req2)

        buyer = req1.buyer_contact
        self.assertEqual(buyer.category_interests.count(), 1)
        self.assertEqual(buyer.category_interests.get().requests_count, 2)

    def test_selected_cities_csv_parsed(self):
        req = create_request(
            phone='77010001122',
            search_scope='custom',
            selected_cities='Алматы, Астана, Караганда',
        )
        sync_buyer_contact_from_request(req)
        buyer = req.buyer_contact

        selected = buyer.city_interests.filter(
            interest_type=BUYER_CITY_INTEREST_SELECTED_CITY,
        )
        self.assertEqual(selected.count(), 3)
        self.assertSetEqual(
            set(selected.values_list('city', flat=True)),
            {'Алматы', 'Астана', 'Караганда'},
        )

    def test_selected_cities_duplicates_removed(self):
        cities = parse_selected_cities('Алматы, алматы, АЛМАТЫ')
        self.assertEqual(cities, ['Алматы'])


class RebuildBuyerContactTests(TestCase):
    def test_category_change_removes_old_interest(self):
        req = create_request(category='Тормоза')
        sync_buyer_contact_from_request(req)
        req.category = 'Охлаждение'
        req.save(update_fields=['category'])
        rebuild_buyer_contact(req.buyer_contact)

        buyer = req.buyer_contact
        categories = set(buyer.category_interests.values_list('category', flat=True))
        self.assertEqual(categories, {'Охлаждение'})

    def test_vehicle_model_change_updates_aggregates(self):
        req = create_request(brand='Toyota', model='Camry')
        sync_buyer_contact_from_request(req)
        req.model = 'Corolla'
        req.save(update_fields=['model'])
        rebuild_buyer_contact(req.buyer_contact)

        buyer = req.buyer_contact
        self.assertEqual(buyer.vehicles.count(), 1)
        self.assertEqual(buyer.vehicles.get().model, 'Corolla')

    def test_city_change_updates_primary_city(self):
        req1 = create_request(phone='77011112233', city='Алматы')
        req2 = create_request(phone='77011112233', city='Астана')
        sync_buyer_contact_from_request(req1)
        sync_buyer_contact_from_request(req2)

        buyer = req1.buyer_contact
        buyer.refresh_from_db()
        self.assertEqual(buyer.primary_city, 'Астана')

    def test_unlinked_request_reduces_counters(self):
        req1 = create_request(phone='77012223344')
        req2 = create_request(phone='77012223344', brand='Honda', model='Civic')
        sync_buyer_contact_from_request(req1)
        sync_buyer_contact_from_request(req2)

        buyer = req1.buyer_contact
        req2.buyer_contact = None
        req2.save(update_fields=['buyer_contact'])
        rebuild_buyer_contact(buyer)

        buyer.refresh_from_db()
        self.assertEqual(buyer.requests_count, 1)

    def test_buyer_without_requests_has_zero_aggregates(self):
        buyer = BuyerContact.objects.create(phone_normalized='77013334455')
        BuyerVehicle.objects.create(
            buyer=buyer,
            transport_type='car',
            brand='Toyota',
            model='Camry',
        )
        BuyerCategoryInterest.objects.create(buyer=buyer, category='Тормоза')
        BuyerCityInterest.objects.create(
            buyer=buyer,
            city='Алматы',
            interest_type=BUYER_CITY_INTEREST_REQUEST_CITY,
        )

        rebuild_buyer_contact(buyer)
        buyer.refresh_from_db()

        self.assertEqual(buyer.requests_count, 0)
        self.assertIsNone(buyer.first_request_at)
        self.assertIsNone(buyer.last_request_at)
        self.assertEqual(buyer.vehicles.count(), 0)
        self.assertEqual(buyer.category_interests.count(), 0)
        self.assertEqual(buyer.city_interests.count(), 0)


class PortalAccessSyncTests(TestCase):
    def test_uses_existing_canonical_portal(self):
        portal = BuyerPortalAccess.objects.create(phone_normalized='77014445566')
        req = create_request(phone='77014445566')
        sync_buyer_contact_from_request(req)

        buyer = req.buyer_contact
        self.assertEqual(buyer.portal_access_id, portal.pk)

    def test_uses_existing_eight_portal_when_canonical_missing(self):
        portal = BuyerPortalAccess.objects.create(phone_normalized='87015556677')
        req = create_request(phone='77015556677')
        result = sync_buyer_contact_from_request(req)

        buyer = req.buyer_contact
        self.assertEqual(buyer.portal_access_id, portal.pk)
        self.assertFalse(result.portal_conflict)

    def test_both_portal_records_mark_conflict(self):
        BuyerPortalAccess.objects.create(phone_normalized='77016667788')
        BuyerPortalAccess.objects.create(phone_normalized='87016667788')
        req = create_request(phone='77016667788')
        result = sync_buyer_contact_from_request(req)

        self.assertTrue(result.portal_conflict)

    def test_existing_portal_access_not_replaced_silently(self):
        canonical = BuyerPortalAccess.objects.create(phone_normalized='77017778899')
        alt = BuyerPortalAccess.objects.create(phone_normalized='87017778899')
        buyer = BuyerContact.objects.create(
            phone_normalized='77017778899',
            portal_access=alt,
        )
        req = create_request(phone='77017778899')
        req.buyer_contact = buyer
        req.save(update_fields=['buyer_contact'])

        result = sync_buyer_contact_from_request(req)
        buyer.refresh_from_db()

        self.assertTrue(result.portal_conflict)
        self.assertEqual(buyer.portal_access_id, alt.pk)
        self.assertNotEqual(buyer.portal_access_id, canonical.pk)


class ConsentSyncTests(TestCase):
    def test_default_consents_created_on_new_buyer(self):
        req = create_request(phone='77018889900')
        sync_buyer_contact_from_request(req)
        buyer = req.buyer_contact

        self.assertEqual(buyer.consents.count(), 3)
        self.assertTrue(
            buyer.consents.filter(
                purpose=CONTACT_CONSENT_PURPOSE_SERVICE,
                status=CONTACT_CONSENT_STATUS_UNKNOWN,
            ).exists(),
        )

    def test_existing_marketing_granted_not_overwritten(self):
        req = create_request(phone='77019990011')
        sync_buyer_contact_from_request(req)
        buyer = req.buyer_contact
        consent = buyer.consents.get(purpose=CONTACT_CONSENT_PURPOSE_MARKETING)
        consent.status = CONTACT_CONSENT_STATUS_GRANTED
        consent.consented_at = timezone.now()
        consent.save()

        sync_buyer_contact_from_request(req)
        consent.refresh_from_db()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)

    def test_existing_revoked_not_overwritten(self):
        req = create_request(phone='77010001122')
        sync_buyer_contact_from_request(req)
        buyer = req.buyer_contact
        consent = buyer.consents.get(purpose=CONTACT_CONSENT_PURPOSE_SERVICE)
        consent.status = CONTACT_CONSENT_STATUS_REVOKED
        consent.revoked_at = timezone.now()
        consent.save()

        sync_buyer_contact_from_request(req)
        consent.refresh_from_db()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)


class RequestLinkConflictTests(TestCase):
    def test_request_linked_to_other_buyer_returns_conflict(self):
        buyer_a = BuyerContact.objects.create(phone_normalized='77011111111')
        buyer_b = BuyerContact.objects.create(phone_normalized='77012222222')
        req = create_request(phone='77012222222')
        req.buyer_contact = buyer_a
        req.save(update_fields=['buyer_contact'])

        result = sync_buyer_contact_from_request(req)
        req.refresh_from_db()

        self.assertEqual(result.status, SYNC_STATUS_REQUEST_LINK_CONFLICT)
        self.assertEqual(req.buyer_contact_id, buyer_a.pk)
        self.assertNotEqual(req.buyer_contact_id, buyer_b.pk)


class RebuildBuyerContactsCommandTests(TestCase):
    def test_dry_run_does_not_create_buyers(self):
        create_request(phone='77011234567')
        out = io.StringIO()
        call_command('rebuild_buyer_contacts', stdout=out)

        self.assertEqual(BuyerContact.objects.count(), 0)
        self.assertIn('DRY RUN', out.getvalue())

    def test_dry_run_does_not_link_requests(self):
        req = create_request(phone='77011234567')
        call_command('rebuild_buyer_contacts', stdout=io.StringIO())
        req.refresh_from_db()

        self.assertIsNone(req.buyer_contact_id)

    def test_apply_creates_and_links(self):
        req = create_request(phone='77011234567')
        call_command('rebuild_buyer_contacts', '--apply', stdout=io.StringIO())
        req.refresh_from_db()

        self.assertEqual(BuyerContact.objects.count(), 1)
        self.assertIsNotNone(req.buyer_contact_id)

    def test_repeat_apply_is_idempotent(self):
        req = create_request(phone='77011234567')
        call_command('rebuild_buyer_contacts', '--apply', stdout=io.StringIO())
        call_command('rebuild_buyer_contacts', '--apply', stdout=io.StringIO())
        req.refresh_from_db()

        self.assertEqual(BuyerContact.objects.count(), 1)
        self.assertEqual(req.buyer_contact.requests_count, 1)

    def test_limit_option(self):
        create_request(phone='77011111111')
        create_request(phone='77012222222')
        call_command(
            'rebuild_buyer_contacts',
            '--apply',
            '--limit',
            '1',
            stdout=io.StringIO(),
        )

        self.assertEqual(BuyerContact.objects.count(), 1)
        self.assertEqual(Request.objects.filter(buyer_contact__isnull=False).count(), 1)

    def test_request_id_option(self):
        req = create_request(phone='77013334455')
        create_request(phone='77014445566')
        call_command(
            'rebuild_buyer_contacts',
            '--apply',
            '--request-id',
            str(req.pk),
            stdout=io.StringIO(),
        )

        req.refresh_from_db()
        self.assertIsNotNone(req.buyer_contact_id)
        self.assertEqual(BuyerContact.objects.count(), 1)
        self.assertEqual(Request.objects.filter(buyer_contact__isnull=False).count(), 1)

    def test_invalid_phone_counted_and_command_continues(self):
        create_request(phone='invalid')
        create_request(phone='77015556677')
        out = io.StringIO()
        call_command('rebuild_buyer_contacts', '--apply', stdout=out)

        self.assertIn('Некорректные номера: 1', out.getvalue())
        self.assertEqual(BuyerContact.objects.count(), 1)
