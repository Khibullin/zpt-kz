from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.models import SellerProfile
from core.models import Seller, SellerLead, SellerLeadContactCandidate
from core.services.seller_directory_audit_campaign import (
    _apply_lead_identity_facts,
    _apply_post_classification_overrides,
    _audit_registered_sellers,
    _store_curated_whatsapp,
    process_seller_directory_audit_batch,
)


class SellerDirectoryAuditCampaignTests(TestCase):
    def test_registered_seller_phone_and_profile_city_are_normalized(self):
        user = get_user_model().objects.create_user(
            username='audit-seller',
            password='test',
        )
        seller = Seller.objects.create(
            name='Audit Seller',
            whatsapp='87772320709',
            user=user,
            transport_type='car',
            city='Алматы',
        )
        profile = SellerProfile.objects.create(
            user=user,
            name='Audit Seller',
            phone='87772320709',
            city='',
        )

        audited, changed = _audit_registered_sellers()

        self.assertEqual(audited, 1)
        self.assertGreaterEqual(changed, 1)
        seller.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(seller.whatsapp, '77772320709')
        self.assertEqual(profile.phone, '77772320709')
        self.assertEqual(profile.city, 'Алматы')

    def test_curated_whatsapp_respects_manual_rejection(self):
        lead = SellerLead.objects.create(
            id=4,
            name='Kaz Avto',
            instagram_username='kaz_avto.kz',
            instagram_url='https://www.instagram.com/kaz_avto.kz/',
            city='Алматы',
        )
        SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77002510460',
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_REJECTED,
            source_url='https://www.instagram.com/kaz_avto.kz/',
        )

        added = _store_curated_whatsapp()

        self.assertEqual(added, 0)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')

    def test_curated_whatsapp_is_saved_only_as_explicit_evidence(self):
        lead = SellerLead.objects.create(
            id=4,
            name='Kaz Avto',
            instagram_username='kaz_avto.kz',
            instagram_url='https://www.instagram.com/kaz_avto.kz/',
            city='Алматы',
        )

        added = _store_curated_whatsapp()

        self.assertEqual(added, 1)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77002510460')
        self.assertEqual(lead.whatsapp_confidence, 'high')
        self.assertEqual(lead.last_enrichment_result, 'verified_whatsapp')

    def test_location_foreign_and_false_positive_cleanup(self):
        shymkent = SellerLead.objects.create(id=17, name='Donix', city='Алматы')
        foreign = SellerLead.objects.create(id=25, name='Bishkek parts', city='Алматы')
        false_positive = SellerLead.objects.create(
            id=61,
            name='Продажа телефонов',
            city='Алматы',
        )

        _apply_lead_identity_facts()
        _apply_post_classification_overrides()

        shymkent.refresh_from_db()
        foreign.refresh_from_db()
        false_positive.refresh_from_db()
        self.assertEqual(shymkent.city, 'Шымкент')
        self.assertEqual(foreign.city, 'Бишкек')
        self.assertEqual(foreign.market_scope, SellerLead.MARKET_SCOPE_FOREIGN)
        self.assertEqual(false_positive.status, SellerLead.STATUS_NOT_SELLER)
        self.assertEqual(false_positive.review_status, SellerLead.REVIEW_REJECTED)
        self.assertEqual(false_positive.lifecycle_status, SellerLead.LIFECYCLE_REJECTED)

    def test_bounded_batch_reclassifies_snapshot_without_creating_sellers(self):
        lead = SellerLead.objects.create(
            id=8,
            name='Запчасти | Пятый Элемент',
            city='Алматы',
            profile_description='Запчасти для корейских и японских авто',
        )

        result = process_seller_directory_audit_batch(
            classification_batch_size=20,
            website_batch_size=1,
        )

        lead.refresh_from_db()
        self.assertEqual(result.registered_audited, 0)
        self.assertEqual(Seller.objects.count(), 0)
        self.assertEqual(lead.business_type, SellerLead.BUSINESS_TYPE_NEW_PARTS)
        self.assertEqual(result.classification_remaining, 0)
