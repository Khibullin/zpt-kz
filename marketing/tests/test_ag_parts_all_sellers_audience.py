from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase

from catalog.models import Product, SellerProfile
from core.models import Country, Seller
from marketing.models import (
    MarketingAudience,
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingWhatsAppTemplate,
)
from marketing.services.audiences.calculators import (
    calculate_audience,
    collect_audience_snapshot,
)
from marketing.services.audiences.constants import (
    GROUP_BUYERS,
    GROUP_SELLERS,
    GROUP_SERVICE_PROVIDERS,
    SUBTYPE_ALL_SELLERS,
    SUBTYPE_COMBINED_SELLERS,
    SUBTYPE_PARTS_REQUESTS,
    SUBTYPE_STO,
)
from marketing.services.audiences.filters import matches_seller_vehicle_scope
from marketing.services.campaigns.ag_parts_wholesale_report import (
    CAMPAIGN_NAME,
    NEW_AUDIENCE_NAME,
    build_ag_parts_wholesale_audience_report,
)
from marketing.services.campaigns.constants import (
    PURPOSE_ALL_SELLERS,
    PURPOSE_COMBINED_SELLERS,
)
from marketing.services.campaigns.preparation import prepare_campaign_snapshot
from marketing.tests.test_marketing_audiences import make_buyer, next_phone
from service_requests.models import ServiceSeller

CHINA_OR_ALL_BRANDS = {
    'seller_countries': ['Китай'],
    'seller_include_all_brands': True,
    'is_active': True,
    'is_test': False,
}


class SellerVehicleScopeFilterTests(TestCase):
    def setUp(self):
        self.china = Country.objects.create(name='Китай')
        self.japan = Country.objects.create(name='Япония')

    def _seller(self, **kwargs) -> Seller:
        defaults = {
            'name': 'Seller',
            'whatsapp': next_phone(),
            'city': 'Алматы',
            'is_active': True,
            'all_brands': False,
            'all_countries': False,
            'is_test_seller': False,
        }
        defaults.update(kwargs)
        return Seller.objects.create(**defaults)

    def _matched_phones(self, criteria=None):
        result = calculate_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_ALL_SELLERS,
            criteria=criteria or CHINA_OR_ALL_BRANDS,
        )
        snapshot = collect_audience_snapshot(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_ALL_SELLERS,
            criteria=criteria or CHINA_OR_ALL_BRANDS,
            purpose=PURPOSE_ALL_SELLERS,
        )
        return result, {row.phone_normalized for row in snapshot.contacts}

    def test_selected_china_is_matched(self):
        seller = self._seller()
        seller.selected_countries.add(self.china)
        result, phones = self._matched_phones()
        self.assertEqual(result.matched_count, 1)
        self.assertIn(seller.whatsapp, phones)

    def test_all_brands_is_matched(self):
        seller = self._seller(all_brands=True)
        result, phones = self._matched_phones()
        self.assertEqual(result.matched_count, 1)
        self.assertIn(seller.whatsapp, phones)

    def test_china_and_all_brands_count_once(self):
        seller = self._seller(all_brands=True)
        seller.selected_countries.add(self.china)
        result, phones = self._matched_phones()
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(phones, {seller.whatsapp})

    def test_only_all_countries_is_not_matched(self):
        self._seller(all_countries=True, all_brands=False)
        result, phones = self._matched_phones()
        self.assertEqual(result.matched_count, 0)
        self.assertEqual(phones, set())

    def test_japan_only_is_not_matched(self):
        seller = self._seller()
        seller.selected_countries.add(self.japan)
        result, _phones = self._matched_phones()
        self.assertEqual(result.matched_count, 0)

    def test_all_countries_does_not_satisfy_country_matcher(self):
        flags = type('Flags', (), {
            'all_brands': False,
            'all_countries': True,
            'selected_country_names': frozenset(),
        })()
        self.assertFalse(
            matches_seller_vehicle_scope(flags, CHINA_OR_ALL_BRANDS),
        )

    def test_inactive_seller_not_matched(self):
        seller = self._seller(is_active=False, all_brands=True)
        result, phones = self._matched_phones()
        self.assertEqual(result.matched_count, 0)
        self.assertNotIn(seller.whatsapp, phones)

    def test_test_seller_not_matched(self):
        seller = self._seller(all_brands=True, is_test_seller=True)
        result, phones = self._matched_phones()
        self.assertEqual(result.matched_count, 0)
        self.assertNotIn(seller.whatsapp, phones)

    def test_sto_only_not_matched(self):
        ServiceSeller.objects.create(
            name='STO',
            whatsapp=next_phone(),
            city='Алматы',
            seller_type='sto',
            password='hash',
            is_active=True,
        )
        result, _phones = self._matched_phones()
        self.assertEqual(result.matched_count, 0)

    def test_marketplace_only_without_seller_scope_not_matched(self):
        phone = next_phone()
        user = User.objects.create_user(f'shop_{phone}', password='secret')
        SellerProfile.objects.create(user=user, name='Shop', phone=phone, city='Алматы')
        Product.objects.create(
            title='Part',
            slug=f'part-{phone}',
            article=f'A-{phone}',
            price=1000,
            whatsapp_number=phone,
            status='active',
        )
        result, phones = self._matched_phones()
        self.assertNotIn(phone, phones)
        self.assertEqual(result.matched_count, 0)


class AllSellersPurposeTests(TestCase):
    def _audience(self) -> MarketingAudience:
        return MarketingAudience.objects.create(
            name=f'Audience {next_phone()}',
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_ALL_SELLERS,
            criteria={},
            is_active=True,
        )

    def _campaign(self, audience, purpose) -> MarketingCampaign:
        user = User.objects.create_user(f'marketer_{next_phone()}', password='secret')
        return MarketingCampaign.objects.create(
            name=f'Campaign {next_phone()}',
            audience=audience,
            purpose=purpose,
            created_by=user,
        )

    def test_marketplace_only_matches_all_sellers_purpose(self):
        phone = next_phone()
        user = User.objects.create_user(f'shop_{phone}', password='secret')
        SellerProfile.objects.create(user=user, name='Shop', phone=phone, city='Алматы')
        Product.objects.create(
            title='Part',
            slug=f'part-{phone}',
            article=f'A-{phone}',
            price=1000,
            whatsapp_number=phone,
            status='active',
        )
        campaign = self._campaign(self._audience(), PURPOSE_ALL_SELLERS)
        prepare_campaign_snapshot(campaign.pk)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertNotEqual(recipient.exclusion_reason, 'audience_rule')

    def test_request_only_matches_all_sellers_purpose(self):
        phone = next_phone()
        Seller.objects.create(name='Parts', whatsapp=phone, city='Алматы', is_active=True)
        campaign = self._campaign(self._audience(), PURPOSE_ALL_SELLERS)
        prepare_campaign_snapshot(campaign.pk)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertNotEqual(recipient.exclusion_reason, 'audience_rule')

    def test_both_roles_match_once(self):
        phone = next_phone()
        Seller.objects.create(name='Both', whatsapp=phone, city='Алматы', is_active=True)
        user = User.objects.create_user(f'both_{phone}', password='secret')
        SellerProfile.objects.create(user=user, name='Both', phone=phone, city='Алматы')
        campaign = self._campaign(self._audience(), PURPOSE_ALL_SELLERS)
        prepare_campaign_snapshot(campaign.pk)
        self.assertEqual(
            campaign.recipients.filter(phone_normalized=phone).count(),
            1,
        )

    def test_combined_sellers_purpose_still_requires_both_roles(self):
        phone = next_phone()
        Seller.objects.create(name='Parts only', whatsapp=phone, city='Алматы', is_active=True)
        audience = MarketingAudience.objects.create(
            name=f'Combined {next_phone()}',
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_COMBINED_SELLERS,
            criteria={},
            is_active=True,
        )
        campaign = self._campaign(audience, PURPOSE_COMBINED_SELLERS)
        prepare_campaign_snapshot(campaign.pk)
        self.assertFalse(campaign.recipients.filter(phone_normalized=phone).exists())

        audience_all = self._audience()
        campaign_all = self._campaign(audience_all, PURPOSE_COMBINED_SELLERS)
        prepare_campaign_snapshot(campaign_all.pk)
        recipient = campaign_all.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.exclusion_reason, 'audience_rule')

    def test_buyer_audience_still_matches(self):
        buyer = make_buyer()
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'is_active': True},
        )
        self.assertGreaterEqual(result.matched_count, 1)
        snapshot = collect_audience_snapshot(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'is_active': True},
            purpose='parts_buyers',
        )
        self.assertIn(buyer.phone_normalized, {
            row.phone_normalized for row in snapshot.contacts
        })

    def test_sto_audience_unbroken(self):
        phone = next_phone()
        ServiceSeller.objects.create(
            name='STO Shop',
            whatsapp=phone,
            city='Алматы',
            seller_type='sto',
            password='hash',
            is_active=True,
        )
        result = calculate_audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
            criteria={},
        )
        self.assertGreaterEqual(result.matched_count, 1)


class AgPartsAllSellersLaunchMigrationTests(TestCase):
    def test_retarget_function_is_idempotent(self):
        from importlib import import_module

        from django.apps import apps

        migration = import_module(
            'marketing.migrations.0016_ag_parts_all_sellers_audience'
        )
        migration.retarget_ag_parts_all_sellers_audience(apps, None)
        migration.retarget_ag_parts_all_sellers_audience(apps, None)
        self.assertEqual(
            MarketingAudience.objects.filter(name=NEW_AUDIENCE_NAME).count(),
            1,
        )
        campaign = MarketingCampaign.objects.get(name=CAMPAIGN_NAME)
        self.assertEqual(campaign.purpose, PURPOSE_ALL_SELLERS)
        self.assertEqual(campaign.audience.name, NEW_AUDIENCE_NAME)

    def test_migrated_campaign_uses_all_sellers_audience(self):
        campaign = MarketingCampaign.objects.get(name=CAMPAIGN_NAME)
        self.assertEqual(campaign.purpose, PURPOSE_ALL_SELLERS)
        self.assertEqual(campaign.status, 'draft')
        self.assertEqual(campaign.audience.name, NEW_AUDIENCE_NAME)
        self.assertEqual(campaign.audience.contact_group, 'sellers')
        self.assertEqual(campaign.audience.contact_subtype, SUBTYPE_ALL_SELLERS)
        self.assertEqual(campaign.audience.criteria.get('seller_countries'), ['Китай'])
        self.assertTrue(campaign.audience.criteria.get('seller_include_all_brands'))
        self.assertTrue(campaign.audience.criteria.get('is_active'))
        self.assertFalse(campaign.audience.criteria.get('is_test'))
        self.assertFalse(campaign.audience.criteria.get('brands'))
        self.assertIsNone(campaign.audience_prepared_at)
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(send_run__campaign=campaign).count(),
            0,
        )

        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name='zpt_ag_parts_wholesale_v1',
            language_code='ru',
        )
        self.assertIn('request_sellers', template.allowed_purposes)
        self.assertIn('marketplace_sellers', template.allowed_purposes)
        self.assertIn('combined_sellers', template.allowed_purposes)
        self.assertIn('all_sellers', template.allowed_purposes)

    def test_report_does_not_send_messages(self):
        MarketingWhatsAppTemplate.objects.filter(
            meta_template_name='zpt_ag_parts_wholesale_v1',
            language_code='ru',
        ).update(meta_status='approved')
        report = build_ag_parts_wholesale_audience_report(prepare=True)
        campaign = MarketingCampaign.objects.get(name=CAMPAIGN_NAME)
        self.assertEqual(report['messages_sent'], 0)
        self.assertEqual(report['campaign_purpose'], PURPOSE_ALL_SELLERS)
        self.assertTrue(report['prepared'])
        self.assertEqual(campaign.send_runs.count(), 0)
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(send_run__campaign=campaign).count(),
            0,
        )
