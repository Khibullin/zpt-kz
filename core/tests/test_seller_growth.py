from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import (
    Brand,
    Country,
    PartCategory,
    Seller,
    SellerContactConsent,
    SellerLead,
    SellerLeadDiscoveredBrand,
    SellerLeadDiscoveredCategory,
)
from core.services.seller_growth import (
    _city_candidate_ids,
    activate_qualified_lead_for_requests,
    city_active_request_seller_count,
    lead_city_is_confirmed,
    lead_is_safe_for_auto_activation,
)


class SellerGrowthActivationTests(TestCase):
    def setUp(self):
        germany = Country.objects.create(name='Германия')
        self.bmw = Brand.objects.create(
            country=germany,
            name='BMW',
            transport_type='car',
        )
        self.engine = PartCategory.objects.create(name='Двигатель')

    def _lead(self, **kwargs):
        defaults = {
            'name': 'BMW Parts Almaty',
            'city': 'Алматы',
            'whatsapp': '77015550001',
            'whatsapp_confidence': 'high',
            'whatsapp_source_url': 'https://example.test/contacts',
            'business_type': 'new_parts',
            'business_type_confidence': 95,
            'market_scope': 'kz',
            'lifecycle_status': SellerLead.LIFECYCLE_CLASSIFIED,
            'status': SellerLead.STATUS_NEEDS_REVIEW,
        }
        defaults.update(kwargs)
        return SellerLead.objects.create(**defaults)

    def test_verified_lead_is_activated_with_discovered_specialization(self):
        lead = self._lead()
        SellerLeadDiscoveredBrand.objects.create(
            seller_lead=lead,
            brand=self.bmw,
            confidence=95,
            source_kind='test',
        )
        SellerLeadDiscoveredCategory.objects.create(
            seller_lead=lead,
            category=self.engine,
            confidence=90,
            source_kind='test',
        )

        self.assertTrue(lead_is_safe_for_auto_activation(lead))
        result = activate_qualified_lead_for_requests(lead)

        self.assertTrue(result.activated)
        self.assertTrue(result.created_seller)
        lead.refresh_from_db()
        seller = lead.request_seller
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_ACTIVE)
        self.assertTrue(seller.receive_requests)
        self.assertTrue(seller.is_active)
        self.assertFalse(seller.is_paused)
        self.assertFalse(seller.all_brands)
        self.assertFalse(seller.all_categories)
        self.assertEqual(list(seller.selected_brands.all()), [self.bmw])
        self.assertEqual(list(seller.selected_categories.all()), [self.engine])
        self.assertEqual(SellerContactConsent.objects.count(), 0)

    def test_existing_registered_seller_preferences_are_not_overwritten(self):
        user = get_user_model().objects.create_user(
            username='77015550002',
            password='test-pass-123',
        )
        seller = Seller.objects.create(
            user=user,
            name='Registered Seller',
            whatsapp='77015550002',
            city='Алматы',
            transport_type='car',
            receive_requests=False,
            is_active=True,
            is_paused=True,
            all_brands=False,
            all_categories=False,
        )
        seller.selected_brands.add(self.bmw)
        lead = self._lead(
            name='Registered Seller Алматы',
            whatsapp='77015550002',
        )

        result = activate_qualified_lead_for_requests(lead)

        self.assertFalse(result.activated)
        self.assertEqual(result.reason, 'existing_seller')
        lead.refresh_from_db()
        seller.refresh_from_db()
        self.assertIsNone(lead.request_seller_id)
        self.assertTrue(seller.is_paused)
        self.assertFalse(seller.receive_requests)
        self.assertEqual(list(seller.selected_brands.all()), [self.bmw])
        self.assertEqual(SellerContactConsent.objects.count(), 0)

    def test_russian_mobile_is_not_auto_activated_as_kazakhstan(self):
        lead = self._lead(
            name='Автозапчасти УРАЛ, отправим по РФ',
            city='Уральск',
            whatsapp='79512325963',
        )

        self.assertFalse(lead_is_safe_for_auto_activation(lead))
        result = activate_qualified_lead_for_requests(lead)
        self.assertFalse(result.activated)
        self.assertEqual(Seller.objects.count(), 0)

    def test_city_must_be_confirmed_by_business_evidence(self):
        lead = self._lead(
            name='Universal Auto Parts',
            city='Конаев',
            whatsapp='77754343424',
        )

        self.assertFalse(lead_city_is_confirmed(lead))
        self.assertFalse(lead_is_safe_for_auto_activation(lead))

    def test_preferred_candidate_from_other_city_is_excluded(self):
        almaty = self._lead(
            name='BMW Parts Almaty',
            city='Алматы',
            whatsapp='77015550004',
        )
        actobe = self._lead(
            name='Автозапчасти Актобе',
            city='Актобе',
            whatsapp='77015550005',
        )

        candidate_ids = _city_candidate_ids(
            'Актобе',
            [almaty.pk, actobe.pk],
            limit=8,
        )

        self.assertIn(actobe.pk, candidate_ids)
        self.assertNotIn(almaty.pk, candidate_ids)

    def test_unresolved_duplicate_is_not_auto_activated(self):
        lead = self._lead()
        other = self._lead(
            name='BMW Parts Duplicate',
            whatsapp='77015550003',
        )
        from core.models import SellerLeadDuplicateMatch

        SellerLeadDuplicateMatch.objects.create(
            lead_a=lead,
            lead_b=other,
            score=85,
            status=SellerLeadDuplicateMatch.STATUS_POSSIBLE,
        )

        self.assertFalse(lead_is_safe_for_auto_activation(lead))
        self.assertFalse(activate_qualified_lead_for_requests(lead).activated)
        self.assertEqual(Seller.objects.count(), 0)

    def test_city_active_count_only_counts_live_request_sellers(self):
        Seller.objects.create(
            name='Live',
            whatsapp='77015550010',
            city='Алматы',
            transport_type='car',
            receive_requests=True,
            is_active=True,
            is_paused=False,
        )
        Seller.objects.create(
            name='Paused',
            whatsapp='77015550011',
            city='Алматы',
            transport_type='car',
            receive_requests=True,
            is_active=True,
            is_paused=True,
        )
        Seller.objects.create(
            name='No receive',
            whatsapp='77015550012',
            city='Алматы',
            transport_type='car',
            receive_requests=False,
            is_active=True,
            is_paused=False,
        )

        self.assertEqual(city_active_request_seller_count('Алматы'), 1)
