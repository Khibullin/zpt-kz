from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    SellerLead,
    normalize_seller_lead_instagram_username,
    normalize_seller_lead_whatsapp,
)


class SellerLeadNormalizationTests(TestCase):
    def test_normalize_instagram_username_strips_at_and_spaces(self):
        self.assertEqual(
            normalize_seller_lead_instagram_username(' @shop_name '),
            'shop_name',
        )

    def test_normalize_whatsapp_to_digits(self):
        self.assertEqual(
            normalize_seller_lead_whatsapp('+7 (701) 123-45-67'),
            '77011234567',
        )

    def test_normalize_whatsapp_converts_leading_eight(self):
        self.assertEqual(
            normalize_seller_lead_whatsapp('87011234567'),
            '77011234567',
        )


class SellerLeadModelTests(TestCase):
    def test_str_with_instagram_username(self):
        lead = SellerLead.objects.create(
            name='Авторазбор Алматы',
            instagram_username='autoshop_almaty',
        )
        self.assertEqual(str(lead), 'Авторазбор Алматы — @autoshop_almaty')

    def test_str_without_instagram_uses_whatsapp(self):
        lead = SellerLead.objects.create(
            name='Магазин запчастей',
            whatsapp='87011234567',
        )
        self.assertEqual(str(lead), 'Магазин запчастей — 77011234567')

    def test_save_normalizes_contacts(self):
        lead = SellerLead.objects.create(
            name='Test Shop',
            instagram_username='@test_shop',
            whatsapp='+7 701 111 22 33',
        )
        lead.refresh_from_db()
        self.assertEqual(lead.instagram_username, 'test_shop')
        self.assertEqual(lead.whatsapp, '77011112233')

    def test_verified_status_sets_checked_at(self):
        lead = SellerLead.objects.create(
            name='Verified Shop',
            status=SellerLead.STATUS_VERIFIED,
        )
        self.assertIsNotNone(lead.checked_at)

    def test_duplicate_instagram_username_blocked(self):
        SellerLead.objects.create(
            name='First Shop',
            instagram_username='duplicate_shop',
        )
        duplicate = SellerLead(
            name='Second Shop',
            instagram_username='@duplicate_shop',
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_duplicate_whatsapp_blocked(self):
        SellerLead.objects.create(
            name='First Shop',
            whatsapp='77011234567',
        )
        duplicate = SellerLead(
            name='Second Shop',
            whatsapp='8 701 123 45 67',
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_empty_whatsapp_and_instagram_allowed_for_multiple_records(self):
        SellerLead.objects.create(name='Lead One')
        SellerLead.objects.create(name='Lead Two')
        self.assertEqual(SellerLead.objects.count(), 2)

    def test_get_instagram_profile_url_from_username(self):
        lead = SellerLead(
            name='Shop',
            instagram_username='shop_name',
        )
        self.assertEqual(
            lead.get_instagram_profile_url(),
            'https://www.instagram.com/shop_name/',
        )

    def test_get_whatsapp_url(self):
        lead = SellerLead(name='Shop', whatsapp='77011234567')
        self.assertEqual(lead.get_whatsapp_url(), 'https://wa.me/77011234567')

    def test_collected_at_set_on_create(self):
        lead = SellerLead.objects.create(name='New Lead')
        self.assertIsNotNone(lead.collected_at)

    def test_default_status_is_needs_review(self):
        lead = SellerLead.objects.create(name='New Lead')
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)

    def test_default_source_type_is_manual(self):
        lead = SellerLead.objects.create(name='New Lead')
        self.assertEqual(lead.source_type, 'manual')

    def test_existing_checked_at_not_overwritten_on_verified_save(self):
        checked_at = timezone.now().replace(year=2024, month=1, day=1)
        lead = SellerLead.objects.create(
            name='Checked Shop',
            status=SellerLead.STATUS_NEEDS_REVIEW,
            checked_at=checked_at,
        )
        lead.status = SellerLead.STATUS_VERIFIED
        lead.save()
        lead.refresh_from_db()
        self.assertEqual(lead.checked_at, checked_at)
