from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core.models import (
    BUYER_CITY_INTEREST_REQUEST_CITY,
    BUYER_CITY_INTEREST_SELECTED_CITY,
    CONTACT_CONSENT_PURPOSE_INFORMATION,
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
)
from core.phone_utils import normalize_kz_phone
from core.services.buyer_contact_utils import mask_phone, normalize_buyer_text


class NormalizeKzPhoneTests(SimpleTestCase):
    def test_valid_seven_prefix(self):
        self.assertEqual(normalize_kz_phone('77011234567'), '77011234567')

    def test_eight_prefix_converted(self):
        self.assertEqual(normalize_kz_phone('87011234567'), '77011234567')

    def test_formatted_number(self):
        self.assertEqual(normalize_kz_phone('+7 701 123 45 67'), '77011234567')

    def test_ten_digits_invalid(self):
        self.assertIsNone(normalize_kz_phone('7011234567'))

    def test_empty_string(self):
        self.assertIsNone(normalize_kz_phone(''))

    def test_none(self):
        self.assertIsNone(normalize_kz_phone(None))

    def test_text_without_digits(self):
        self.assertIsNone(normalize_kz_phone('invalid'))

    def test_too_long_number(self):
        self.assertIsNone(normalize_kz_phone('770112345678901'))

    def test_unexpected_type(self):
        self.assertIsNone(normalize_kz_phone(['77011234567']))


class NormalizeBuyerTextTests(SimpleTestCase):
    def test_casefold_and_spaces(self):
        self.assertEqual(normalize_buyer_text('  Toyota   Camry '), 'toyota camry')

    def test_none_returns_empty(self):
        self.assertEqual(normalize_buyer_text(None), '')

    def test_preserves_hyphen(self):
        self.assertEqual(normalize_buyer_text('Mercedes-Benz'), 'mercedes-benz')


class MaskPhoneTests(SimpleTestCase):
    def test_masks_eleven_digit_number(self):
        self.assertEqual(mask_phone('77011234567'), '7701***4567')


class BuyerContactModelTests(TestCase):
    def test_unique_phone_normalized(self):
        BuyerContact.objects.create(phone_normalized='77011234567')
        with self.assertRaises(IntegrityError):
            BuyerContact.objects.create(phone_normalized='77011234567')

    def test_default_counters(self):
        buyer = BuyerContact.objects.create(phone_normalized='77012223344')
        self.assertEqual(buyer.requests_count, 0)
        self.assertEqual(buyer.city_scope_requests_count, 0)
        self.assertEqual(buyer.kazakhstan_scope_requests_count, 0)
        self.assertEqual(buyer.custom_scope_requests_count, 0)

    def test_str_masks_phone(self):
        buyer = BuyerContact.objects.create(phone_normalized='77013334455')
        self.assertEqual(str(buyer), '7701***4455')

    def test_optional_portal_access_link(self):
        portal = BuyerPortalAccess.objects.create(phone_normalized='77014445566')
        buyer = BuyerContact.objects.create(
            phone_normalized='77014445566',
            portal_access=portal,
        )
        self.assertEqual(buyer.portal_access_id, portal.pk)
        self.assertEqual(portal.buyer_contact.pk, buyer.pk)


class BuyerVehicleModelTests(TestCase):
    def setUp(self):
        self.buyer = BuyerContact.objects.create(phone_normalized='77015556677')

    def test_normalized_fields_filled_on_save(self):
        vehicle = BuyerVehicle.objects.create(
            buyer=self.buyer,
            transport_type='car',
            brand='Toyota',
            model='Camry',
        )
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.brand_normalized, 'toyota')
        self.assertEqual(vehicle.model_normalized, 'camry')

    def test_same_brand_different_case_is_one_vehicle(self):
        BuyerVehicle.objects.create(
            buyer=self.buyer,
            transport_type='car',
            brand='Toyota',
            model='Camry',
        )
        with self.assertRaises(IntegrityError):
            BuyerVehicle.objects.create(
                buyer=self.buyer,
                transport_type='car',
                brand='toyota',
                model='camry',
            )

    def test_different_models_create_different_records(self):
        BuyerVehicle.objects.create(
            buyer=self.buyer,
            transport_type='car',
            brand='Toyota',
            model='Camry',
        )
        BuyerVehicle.objects.create(
            buyer=self.buyer,
            transport_type='car',
            brand='Toyota',
            model='RAV4',
        )
        self.assertEqual(self.buyer.vehicles.count(), 2)

    def test_different_transport_types_create_different_records(self):
        BuyerVehicle.objects.create(
            buyer=self.buyer,
            transport_type='car',
            brand='Toyota',
            model='Hiace',
        )
        BuyerVehicle.objects.create(
            buyer=self.buyer,
            transport_type='truck',
            brand='Toyota',
            model='Hiace',
        )
        self.assertEqual(self.buyer.vehicles.count(), 2)


class BuyerCategoryInterestModelTests(TestCase):
    def setUp(self):
        self.buyer = BuyerContact.objects.create(phone_normalized='77016667788')

    def test_same_category_different_case_not_duplicated(self):
        BuyerCategoryInterest.objects.create(
            buyer=self.buyer,
            category='Тормоза',
        )
        with self.assertRaises(IntegrityError):
            BuyerCategoryInterest.objects.create(
                buyer=self.buyer,
                category='тормоза',
            )


class BuyerCityInterestModelTests(TestCase):
    def setUp(self):
        self.buyer = BuyerContact.objects.create(phone_normalized='77017778899')

    def test_same_city_different_case_not_duplicated(self):
        BuyerCityInterest.objects.create(
            buyer=self.buyer,
            city='Алматы',
            interest_type=BUYER_CITY_INTEREST_REQUEST_CITY,
        )
        with self.assertRaises(IntegrityError):
            BuyerCityInterest.objects.create(
                buyer=self.buyer,
                city='алматы',
                interest_type=BUYER_CITY_INTEREST_REQUEST_CITY,
            )

    def test_request_city_and_selected_city_are_separate(self):
        BuyerCityInterest.objects.create(
            buyer=self.buyer,
            city='Алматы',
            interest_type=BUYER_CITY_INTEREST_REQUEST_CITY,
        )
        BuyerCityInterest.objects.create(
            buyer=self.buyer,
            city='Алматы',
            interest_type=BUYER_CITY_INTEREST_SELECTED_CITY,
        )
        self.assertEqual(self.buyer.city_interests.count(), 2)


class ContactConsentModelTests(TestCase):
    def setUp(self):
        self.buyer = BuyerContact.objects.create(phone_normalized='77018889900')
        self.now = timezone.now()

    def test_default_status_unknown(self):
        consent = ContactConsent.objects.create(
            buyer=self.buyer,
            channel='whatsapp',
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_UNKNOWN)

    def test_unique_purpose_per_buyer(self):
        ContactConsent.objects.create(
            buyer=self.buyer,
            channel='whatsapp',
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
        with self.assertRaises(ValidationError):
            ContactConsent.objects.create(
                buyer=self.buyer,
                channel='whatsapp',
                purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            )

    def test_granted_without_consented_at_fails_validation(self):
        consent = ContactConsent(
            buyer=self.buyer,
            channel='whatsapp',
            purpose=CONTACT_CONSENT_PURPOSE_SERVICE,
            status=CONTACT_CONSENT_STATUS_GRANTED,
        )
        with self.assertRaises(ValidationError):
            consent.full_clean()

    def test_revoked_without_revoked_at_fails_validation(self):
        consent = ContactConsent(
            buyer=self.buyer,
            channel='whatsapp',
            purpose=CONTACT_CONSENT_PURPOSE_INFORMATION,
            status=CONTACT_CONSENT_STATUS_REVOKED,
            consented_at=self.now - timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            consent.full_clean()

    def test_service_and_marketing_consents_are_separate(self):
        ContactConsent.objects.create(
            buyer=self.buyer,
            channel='whatsapp',
            purpose=CONTACT_CONSENT_PURPOSE_SERVICE,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            consented_at=self.now,
        )
        ContactConsent.objects.create(
            buyer=self.buyer,
            channel='whatsapp',
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_UNKNOWN,
        )
        self.assertEqual(self.buyer.consents.count(), 2)

    def test_granted_save_requires_consented_at(self):
        consent = ContactConsent(
            buyer=self.buyer,
            channel='whatsapp',
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
        )
        with self.assertRaises(ValidationError):
            consent.save()

    def test_revoked_keeps_consented_at(self):
        consented_at = self.now - timedelta(days=10)
        revoked_at = self.now
        consent = ContactConsent.objects.create(
            buyer=self.buyer,
            channel='whatsapp',
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_REVOKED,
            consented_at=consented_at,
            revoked_at=revoked_at,
        )
        self.assertEqual(consent.consented_at, consented_at)
        self.assertEqual(consent.revoked_at, revoked_at)
