from __future__ import annotations

import re

from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CONTACT_STATUS_BLOCKED,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerBroadcastCampaign,
    BuyerBroadcastRecipient,
    BuyerContact,
    ContactConsent,
    Seller,
)
from core.services.buyer_contact_utils import mask_phone
from marketing.models import (
    MarketingAudience,
    MarketingCampaign,
    MarketingCampaignRecipient,
)
from marketing.services.audiences.constants import (
    GROUP_BUYERS,
    GROUP_SELLERS,
    GROUP_SERVICE_PROVIDERS,
    GROUP_TEST,
    SUBTYPE_ALL_BUYERS,
    SUBTYPE_ALL_SELLERS,
    SUBTYPE_ALL_SERVICE_PROVIDERS,
    SUBTYPE_COMBINED_SELLERS,
    SUBTYPE_DETAILING,
    SUBTYPE_MARKETPLACE_SELLERS,
    SUBTYPE_PARTS_REQUESTS,
    SUBTYPE_REQUEST_SELLERS,
    SUBTYPE_STO,
    SUBTYPE_TEST_CONTACTS,
)
from marketing.services.campaigns.constants import (
    EXCLUSION_AUDIENCE_RULE,
    PURPOSE_COMBINED_SELLERS,
    PURPOSE_DETAILING_PROVIDERS,
    PURPOSE_MARKETPLACE_BUYERS,
    PURPOSE_MARKETPLACE_SELLERS,
    PURPOSE_PARTS_BUYERS,
    PURPOSE_REQUEST_SELLERS,
    PURPOSE_SERVICE_CUSTOMERS,
    PURPOSE_STO_PROVIDERS,
    PURPOSE_TEST_CAMPAIGN,
    STATUS_AUDIENCE_PREPARED,
    STATUS_ARCHIVED,
    STATUS_CANCELLED,
    STATUS_DRAFT,
)
from marketing.services.campaigns.signatures import compute_audience_signature
from marketing.services.campaigns.preparation import prepare_campaign_snapshot
from marketing.tests.test_marketing_audiences import (
    grant_consent,
    grant_marketing_permission,
    make_buyer,
    next_phone,
)
from catalog.models import Product, SellerProfile
from django.contrib.auth.models import User as AuthUser
from orders.models import Order
from service_requests.models import Service, ServiceRequest, ServiceSeller

_phone_counter = 9400000


def next_seller_phone() -> str:
    global _phone_counter
    _phone_counter += 1
    return f'77{_phone_counter:09d}'[-11:]


def make_audience(**kwargs) -> MarketingAudience:
    defaults = {
        'name': f'Audience {next_phone()}',
        'contact_group': GROUP_BUYERS,
        'contact_subtype': SUBTYPE_PARTS_REQUESTS,
        'criteria': {},
        'is_active': True,
    }
    defaults.update(kwargs)
    return MarketingAudience.objects.create(**defaults)


def make_campaign(audience: MarketingAudience, user: User, **kwargs) -> MarketingCampaign:
    defaults = {
        'name': f'Campaign {next_phone()}',
        'audience': audience,
        'purpose': PURPOSE_PARTS_BUYERS,
        'created_by': user,
    }
    defaults.update(kwargs)
    return MarketingCampaign.objects.create(**defaults)


class MarketingCampaignAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)

    def test_permission_required(self):
        response = self.client.get(reverse('marketing:campaigns'))
        self.assertEqual(response.status_code, 302)
        self.client.login(username='marketer', password='secret')
        response = self.client.get(reverse('marketing:campaigns'))
        self.assertEqual(response.status_code, 200)


class MarketingCampaignCrudTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.audience = make_audience()

    def test_create_draft_campaign(self):
        response = self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': 'Тестовая кампания',
                'description': 'Описание',
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': self.audience.pk,
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        campaign = MarketingCampaign.objects.get(name='Тестовая кампания')
        self.assertEqual(campaign.status, STATUS_DRAFT)
        self.assertEqual(campaign.created_by, self.user)

    def test_name_required(self):
        response = self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': '',
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': self.audience.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingCampaign.objects.filter(audience=self.audience).exists())

    def test_compatible_audience_accepted(self):
        audience = make_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_ALL_BUYERS,
        )
        response = self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': 'Совместимая',
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': audience.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(MarketingCampaign.objects.filter(name='Совместимая').exists())

    def test_incompatible_audience_rejected(self):
        audience = make_audience(
            name='Seller audience',
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
        )
        response = self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': 'Несовместимая',
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': audience.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingCampaign.objects.filter(name='Несовместимая').exists())

    def test_audience_id_spoofing_rejected(self):
        compatible = make_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_ALL_BUYERS,
        )
        incompatible = make_audience(
            name='Hidden seller audience',
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
        )
        response = self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': 'Подмена',
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': incompatible.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingCampaign.objects.filter(name='Подмена').exists())
        self.assertFalse(
            MarketingCampaign.objects.filter(audience=incompatible).exists(),
        )


class MarketingCampaignPrepareTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.audience = make_audience()
        self.campaign = make_campaign(self.audience, self.user)

    def test_prepare_only_post(self):
        response = self.client.get(
            reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}),
        )
        self.assertEqual(response.status_code, 405)
        self.assertFalse(self.campaign.recipients.exists())

    def test_prepare_creates_snapshot(self):
        buyer = make_buyer()
        grant_consent(buyer)
        response = self.client.post(
            reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}),
        )
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, STATUS_AUDIENCE_PREPARED)
        self.assertTrue(self.campaign.recipients.exists())

    def test_recipients_deduplicated_by_phone(self):
        buyer = make_buyer()
        grant_consent(buyer)
        prepare_campaign_snapshot(self.campaign.pk)
        phones = list(
            self.campaign.recipients.values_list('phone_normalized', flat=True),
        )
        self.assertEqual(len(phones), len(set(phones)))

    def test_repeat_prepare_replaces_snapshot(self):
        buyer = make_buyer()
        grant_consent(buyer)
        prepare_campaign_snapshot(self.campaign.pk)
        first_ids = set(self.campaign.recipients.values_list('pk', flat=True))
        inactive = make_buyer(status=BUYER_CONTACT_STATUS_BLOCKED)
        grant_consent(inactive)
        prepare_campaign_snapshot(self.campaign.pk)
        second_ids = set(self.campaign.recipients.values_list('pk', flat=True))
        self.assertNotEqual(first_ids, second_ids)
        self.assertEqual(self.campaign.recipients.count(), 2)


class MarketingCampaignEligibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        self.audience = make_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_ALL_BUYERS,
        )
        self.campaign = make_campaign(self.audience, self.user)

    def _prepare(self):
        prepare_campaign_snapshot(self.campaign.pk)
        self.campaign.refresh_from_db()

    def test_granted_active_buyer_eligible(self):
        buyer = make_buyer()
        grant_consent(buyer)
        self._prepare()
        recipient = self.campaign.recipients.get(phone_normalized=buyer.phone_normalized)
        self.assertEqual(recipient.eligibility_status, 'eligible')

    def test_unknown_buyer_excluded(self):
        buyer = make_buyer()
        grant_consent(buyer, status=CONTACT_CONSENT_STATUS_UNKNOWN)
        self._prepare()
        recipient = self.campaign.recipients.get(phone_normalized=buyer.phone_normalized)
        self.assertEqual(recipient.eligibility_status, 'excluded')
        self.assertEqual(recipient.exclusion_reason, 'consent_unknown')

    def test_revoked_buyer_excluded(self):
        buyer = make_buyer()
        grant_consent(buyer, status=CONTACT_CONSENT_STATUS_REVOKED)
        self._prepare()
        recipient = self.campaign.recipients.get(phone_normalized=buyer.phone_normalized)
        self.assertEqual(recipient.exclusion_reason, 'consent_revoked')

    def test_inactive_excluded(self):
        buyer = make_buyer(status=BUYER_CONTACT_STATUS_BLOCKED)
        grant_consent(buyer)
        self._prepare()
        recipient = self.campaign.recipients.get(phone_normalized=buyer.phone_normalized)
        self.assertEqual(recipient.exclusion_reason, 'inactive')

    def test_test_excluded_from_regular_campaign(self):
        self.audience.contact_group = GROUP_BUYERS
        self.audience.contact_subtype = SUBTYPE_PARTS_REQUESTS
        self.audience.save()
        self.campaign.audience = self.audience
        self.campaign.save()
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        self._prepare()
        recipient = self.campaign.recipients.get(phone_normalized=buyer.phone_normalized)
        self.assertEqual(recipient.exclusion_reason, 'test_contact')
        self.assertEqual(self.campaign.test_count, 1)
        self.assertEqual(self.campaign.eligible_count, 0)

    def test_test_campaign_only_test_granted_active(self):
        test_audience = make_audience(
            name='Test audience',
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        campaign = make_campaign(
            test_audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            name='Test campaign',
        )
        test_buyer = make_buyer(is_test_contact=True)
        grant_consent(test_buyer)
        real_buyer = make_buyer(is_test_contact=False)
        grant_consent(real_buyer)
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        phones = set(campaign.recipients.values_list('phone_normalized', flat=True))
        self.assertIn(test_buyer.phone_normalized, phones)
        self.assertNotIn(real_buyer.phone_normalized, phones)
        self.assertEqual(campaign.eligible_count, 1)
        self.assertEqual(campaign.test_count, 1)
        self.assertEqual(campaign.excluded_count, 0)

    def test_test_campaign_snapshot_test_count_two(self):
        test_audience = make_audience(
            name='Test audience',
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        campaign = make_campaign(
            test_audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            name='Test campaign two',
        )
        for _ in range(2):
            buyer = make_buyer(is_test_contact=True)
            grant_consent(buyer)
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.matched_count, 2)
        self.assertEqual(campaign.unique_count, 2)
        self.assertEqual(campaign.test_count, 2)
        self.assertEqual(campaign.eligible_count, 2)
        self.assertEqual(campaign.excluded_count, 0)

    def test_regular_campaign_test_count_excludes_from_eligible(self):
        self.audience.contact_group = GROUP_BUYERS
        self.audience.contact_subtype = SUBTYPE_PARTS_REQUESTS
        self.audience.save()
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        self._prepare()
        recipient = self.campaign.recipients.get(phone_normalized=buyer.phone_normalized)
        self.assertEqual(self.campaign.test_count, 1)
        self.assertEqual(self.campaign.eligible_count, 0)
        self.assertEqual(recipient.exclusion_reason, 'test_contact')

    def test_repeat_prepare_does_not_double_test_count(self):
        test_audience = make_audience(
            name='Test audience repeat',
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        campaign = make_campaign(
            test_audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            name='Repeat prepare',
        )
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.test_count, 1)
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.test_count, 1)
        self.assertEqual(
            campaign.eligible_count + campaign.excluded_count,
            campaign.unique_count,
        )

    def test_seller_without_consent_not_eligible(self):
        audience = make_audience(
            name='Sellers',
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
        )
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_REQUEST_SELLERS,
            name='Seller campaign',
        )
        Seller.objects.create(
            name='Seller',
            whatsapp=next_seller_phone(),
            city='Алматы',
            is_active=True,
        )
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.eligible_count, 0)
        self.assertEqual(campaign.consent_not_recorded_count, 1)

    def test_sto_without_consent_not_eligible(self):
        audience = make_audience(
            name='STO audience',
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
        )
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_STO_PROVIDERS,
            name='STO campaign',
        )
        ServiceSeller.objects.create(
            name='STO',
            whatsapp=next_seller_phone(),
            city='Алматы',
            seller_type='sto',
            password='hash',
            is_active=True,
        )
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.eligible_count, 0)


class MarketingCampaignSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.audience = make_audience()
        self.campaign = make_campaign(self.audience, self.user)

    def test_detail_html_no_full_phone(self):
        buyer = make_buyer()
        grant_consent(buyer)
        self.client.post(reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}))
        response = self.client.get(
            reverse('marketing:campaign_detail', kwargs={'pk': self.campaign.pk}),
        )
        html = response.content.decode('utf-8')
        self.assertNotIn(buyer.phone_normalized, html)
        self.assertIn(mask_phone(buyer.phone_normalized), html)

    def test_no_full_phone_in_json_or_data_attrs(self):
        buyer = make_buyer()
        grant_consent(buyer)
        self.client.post(reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}))
        response = self.client.get(
            reverse('marketing:campaign_detail', kwargs={'pk': self.campaign.pk}),
        )
        html = response.content.decode('utf-8')
        self.assertNotIn(buyer.phone_normalized, html)
        self.assertIsNone(re.search(r'data-[a-z-]*="' + re.escape(buyer.phone_normalized), html))

    def test_preview_max_50(self):
        for _ in range(55):
            buyer = make_buyer()
            grant_consent(buyer)
        self.client.post(reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}))
        response = self.client.get(
            reverse('marketing:campaign_detail', kwargs={'pk': self.campaign.pk}),
        )
        rows = response.content.decode('utf-8').count('marketing-table')
        self.campaign.refresh_from_db()
        self.assertGreater(self.campaign.unique_count, 50)
        self.assertEqual(
            self.campaign.recipients.count(),
            self.campaign.unique_count,
        )
        preview_count = len(re.findall(r'<td>[^<]*\*\*\*[^<]*</td>', response.content.decode('utf-8')))
        self.assertLessEqual(preview_count, 50)

    def test_no_send_url(self):
        urls = [
            reverse('marketing:campaigns'),
            reverse('marketing:campaign_create'),
            reverse('marketing:campaign_detail', kwargs={'pk': self.campaign.pk}),
            reverse('marketing:campaign_edit', kwargs={'pk': self.campaign.pk}),
        ]
        forbidden = (
            'send_whatsapp',
            'отправить сейчас',
            'поставить в очередь',
            'запустить',
        )
        for url in urls:
            response = self.client.get(url)
            self.assertIn(response.status_code, (200, 302))
            content = response.content.decode('utf-8').lower()
            for token in forbidden:
                self.assertNotIn(token, content)

    def test_no_send_button(self):
        self.client.post(reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}))
        response = self.client.get(
            reverse('marketing:campaign_detail', kwargs={'pk': self.campaign.pk}),
        )
        content = response.content.decode('utf-8').lower()
        self.assertNotIn('>отправить<', content)
        self.assertNotIn('>запустить<', content)

    def test_buyer_broadcast_recipient_not_created(self):
        buyer = make_buyer()
        grant_consent(buyer)
        before = BuyerBroadcastRecipient.objects.count()
        self.client.post(reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}))
        self.assertEqual(BuyerBroadcastRecipient.objects.count(), before)

    def test_buyer_broadcast_campaign_unchanged(self):
        before = BuyerBroadcastCampaign.objects.count()
        buyer = make_buyer()
        grant_consent(buyer)
        self.client.post(reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}))
        self.assertEqual(BuyerBroadcastCampaign.objects.count(), before)


class MarketingCampaignLifecycleTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.audience = make_audience()
        self.campaign = make_campaign(self.audience, self.user)

    def _prepare(self):
        buyer = make_buyer()
        grant_consent(buyer)
        self.client.post(reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}))
        self.campaign.refresh_from_db()

    def test_edit_audience_resets_snapshot(self):
        self._prepare()
        self.assertTrue(self.campaign.has_prepared_snapshot)
        new_audience = make_audience(
            name='Another audience',
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_ALL_BUYERS,
        )
        self.client.post(
            reverse('marketing:campaign_edit', kwargs={'pk': self.campaign.pk}),
            {
                'name': self.campaign.name,
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': new_audience.pk,
                'is_active': 'on',
            },
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, STATUS_DRAFT)
        self.assertIsNone(self.campaign.audience_prepared_at)
        self.assertFalse(self.campaign.recipients.exists())
        self.assertEqual(self.campaign.eligible_count, 0)

    def test_edit_name_keeps_snapshot(self):
        self._prepare()
        recipient_count = self.campaign.recipients.count()
        self.client.post(
            reverse('marketing:campaign_edit', kwargs={'pk': self.campaign.pk}),
            {
                'name': 'Новое название',
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': self.audience.pk,
                'is_active': 'on',
            },
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.name, 'Новое название')
        self.assertEqual(self.campaign.status, STATUS_AUDIENCE_PREPARED)
        self.assertEqual(self.campaign.recipients.count(), recipient_count)

    def test_stale_after_criteria_change(self):
        self._prepare()
        audience = self.campaign.audience
        audience.criteria = {'primary_cities': ['Алматы']}
        audience.save()
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_snapshot_stale())

    def test_updated_at_only_does_not_make_stale(self):
        self._prepare()
        MarketingAudience.objects.filter(pk=self.audience.pk).update(
            updated_at=timezone.now(),
        )
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_snapshot_stale())

    def test_copy_does_not_copy_recipients(self):
        self._prepare()
        count_before = MarketingCampaignRecipient.objects.count()
        response = self.client.post(
            reverse('marketing:campaign_copy', kwargs={'pk': self.campaign.pk}),
        )
        self.assertEqual(response.status_code, 302)
        copy = MarketingCampaign.objects.exclude(pk=self.campaign.pk).get()
        self.assertEqual(copy.status, STATUS_DRAFT)
        self.assertFalse(copy.recipients.exists())
        self.assertEqual(MarketingCampaignRecipient.objects.count(), count_before)

    def test_cancel_keeps_snapshot(self):
        self._prepare()
        count = self.campaign.recipients.count()
        self.client.post(reverse('marketing:campaign_cancel', kwargs={'pk': self.campaign.pk}))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, STATUS_CANCELLED)
        self.assertEqual(self.campaign.recipients.count(), count)

    def test_archive_keeps_snapshot(self):
        self._prepare()
        count = self.campaign.recipients.count()
        self.client.post(reverse('marketing:campaign_archive', kwargs={'pk': self.campaign.pk}))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, STATUS_ARCHIVED)
        self.assertEqual(self.campaign.recipients.count(), count)

    def test_delete_only_draft_without_snapshot(self):
        response = self.client.post(
            reverse('marketing:campaign_delete', kwargs={'pk': self.campaign.pk}),
            {'confirm': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(MarketingCampaign.objects.filter(pk=self.campaign.pk).exists())

    def test_delete_blocked_with_snapshot(self):
        self._prepare()
        response = self.client.get(
            reverse('marketing:campaign_delete', kwargs={'pk': self.campaign.pk}),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(MarketingCampaign.objects.filter(pk=self.campaign.pk).exists())

    def test_get_does_not_modify_data(self):
        buyer = make_buyer()
        grant_consent(buyer)
        self.client.post(reverse('marketing:campaign_prepare', kwargs={'pk': self.campaign.pk}))
        self.campaign.refresh_from_db()
        audience_updated = self.audience.updated_at
        campaign_updated = self.campaign.updated_at
        recipient_count = self.campaign.recipients.count()
        self.client.get(reverse('marketing:campaign_detail', kwargs={'pk': self.campaign.pk}))
        self.client.get(reverse('marketing:campaigns'))
        self.audience.refresh_from_db()
        self.campaign.refresh_from_db()
        self.assertEqual(self.audience.updated_at, audience_updated)
        self.assertEqual(self.campaign.updated_at, campaign_updated)
        self.assertEqual(self.campaign.recipients.count(), recipient_count)


class MarketingCampaignPurposeFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)

    def _audience(self, **kwargs) -> MarketingAudience:
        defaults = {
            'name': f'Audience {next_phone()}',
            'contact_group': GROUP_BUYERS,
            'contact_subtype': SUBTYPE_ALL_BUYERS,
            'criteria': {},
            'is_active': True,
        }
        defaults.update(kwargs)
        return MarketingAudience.objects.create(**defaults)

    def _campaign(self, audience: MarketingAudience, purpose: str) -> MarketingCampaign:
        return MarketingCampaign.objects.create(
            name=f'Campaign {next_phone()}',
            audience=audience,
            purpose=purpose,
            created_by=self.user,
        )

    def _prepare(self, campaign: MarketingCampaign) -> MarketingCampaign:
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        return campaign

    def test_parts_buyers_all_buyers_excludes_marketplace_only(self):
        phone = next_phone()
        Order.objects.create(
            customer_name='Market buyer',
            customer_phone=phone,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
            status=Order.STATUS_PAID,
        )
        campaign = self._campaign(self._audience(), PURPOSE_PARTS_BUYERS)
        self._prepare(campaign)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.exclusion_reason, EXCLUSION_AUDIENCE_RULE)
        self.assertEqual(campaign.eligible_count, 0)

    def test_parts_buyers_all_buyers_excludes_service_only(self):
        phone = next_phone()
        ServiceRequest.objects.create(service_type='sto', city='Алматы', phone=phone)
        campaign = self._campaign(self._audience(), PURPOSE_PARTS_BUYERS)
        self._prepare(campaign)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.exclusion_reason, EXCLUSION_AUDIENCE_RULE)
        self.assertEqual(campaign.eligible_count, 0)

    def test_marketplace_buyers_all_buyers_allows_paid_marketplace_buyer(self):
        phone = next_phone()
        buyer = make_buyer(phone_normalized=phone)
        grant_consent(buyer)
        Order.objects.create(
            customer_name='Market buyer',
            customer_phone=phone,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
            status=Order.STATUS_PAID,
        )
        campaign = self._campaign(self._audience(), PURPOSE_MARKETPLACE_BUYERS)
        self._prepare(campaign)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.eligibility_status, 'eligible')

    def test_service_customers_all_buyers_allows_service_customer(self):
        phone = next_phone()
        buyer = make_buyer(phone_normalized=phone)
        grant_consent(buyer)
        ServiceRequest.objects.create(service_type='sto', city='Алматы', phone=phone)
        campaign = self._campaign(self._audience(), PURPOSE_SERVICE_CUSTOMERS)
        self._prepare(campaign)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.eligibility_status, 'eligible')

    def test_request_sellers_all_sellers_excludes_marketplace_only(self):
        phone = next_phone()
        user = AuthUser.objects.create_user(f'seller_{phone}', password='secret')
        SellerProfile.objects.create(user=user, name='Shop', phone=phone, city='Алматы')
        Product.objects.create(
            title='Part',
            slug=f'part-{phone}',
            article=f'A-{phone}',
            price=1000,
            whatsapp_number=phone,
            status='active',
        )
        audience = self._audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_ALL_SELLERS,
        )
        campaign = self._campaign(audience, PURPOSE_REQUEST_SELLERS)
        self._prepare(campaign)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.exclusion_reason, EXCLUSION_AUDIENCE_RULE)

    def test_marketplace_sellers_all_sellers_excludes_request_only(self):
        phone = next_phone()
        Seller.objects.create(name='Parts seller', whatsapp=phone, city='Алматы', is_active=True)
        audience = self._audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_ALL_SELLERS,
        )
        campaign = self._campaign(audience, PURPOSE_MARKETPLACE_SELLERS)
        self._prepare(campaign)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.exclusion_reason, EXCLUSION_AUDIENCE_RULE)

    def test_combined_sellers_requires_both_roles(self):
        phone = next_phone()
        Seller.objects.create(name='Parts only', whatsapp=phone, city='Алматы', is_active=True)
        audience = self._audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_ALL_SELLERS,
        )
        campaign = self._campaign(audience, PURPOSE_COMBINED_SELLERS)
        self._prepare(campaign)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.exclusion_reason, EXCLUSION_AUDIENCE_RULE)

    def test_sto_providers_all_service_providers_excludes_detailing_only(self):
        phone = next_phone()
        ServiceSeller.objects.create(
            name='Detailing',
            whatsapp=phone,
            city='Алматы',
            seller_type='detailing',
            password='hash',
            is_active=True,
        )
        audience = self._audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_ALL_SERVICE_PROVIDERS,
        )
        campaign = self._campaign(audience, PURPOSE_STO_PROVIDERS)
        self._prepare(campaign)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.exclusion_reason, EXCLUSION_AUDIENCE_RULE)

    def test_detailing_providers_excludes_sto_only(self):
        phone = next_phone()
        ServiceSeller.objects.create(
            name='STO',
            whatsapp=phone,
            city='Алматы',
            seller_type='sto',
            password='hash',
            is_active=True,
        )
        audience = self._audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_ALL_SERVICE_PROVIDERS,
        )
        campaign = self._campaign(audience, PURPOSE_DETAILING_PROVIDERS)
        self._prepare(campaign)
        recipient = campaign.recipients.get(phone_normalized=phone)
        self.assertEqual(recipient.exclusion_reason, EXCLUSION_AUDIENCE_RULE)


class MarketingCampaignStaleSignatureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        self.audience = make_audience()
        self.campaign = make_campaign(self.audience, self.user)

    def _prepare(self):
        prepare_campaign_snapshot(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.audience.refresh_from_db()

    def test_not_stale_immediately_after_prepare(self):
        self._prepare()
        self.assertFalse(self.campaign.is_snapshot_stale())
        self.assertEqual(
            self.campaign.audience_signature_at_prepare,
            compute_audience_signature(self.audience),
        )

    def test_recalculate_same_criteria_not_stale(self):
        self._prepare()
        buyer = make_buyer()
        grant_consent(buyer)
        self.audience.last_calculated_at = timezone.now()
        self.audience.last_matched_count = 1
        self.audience.last_eligible_count = 1
        self.audience.save(
            update_fields=[
                'last_calculated_at',
                'last_matched_count',
                'last_eligible_count',
            ],
        )
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_snapshot_stale())

    def test_last_calculated_at_change_not_stale(self):
        self._prepare()
        MarketingAudience.objects.filter(pk=self.audience.pk).update(
            last_calculated_at=timezone.now(),
        )
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_snapshot_stale())

    def test_last_matched_count_change_not_stale(self):
        self._prepare()
        MarketingAudience.objects.filter(pk=self.audience.pk).update(last_matched_count=999)
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_snapshot_stale())

    def test_rename_audience_not_stale(self):
        self._prepare()
        self.audience.name = 'Renamed audience'
        self.audience.save()
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_snapshot_stale())

    def test_description_change_not_stale(self):
        self._prepare()
        self.audience.description = 'Updated description'
        self.audience.save()
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_snapshot_stale())

    def test_criteria_change_makes_stale(self):
        self._prepare()
        self.audience.criteria = {'primary_cities': ['Алматы']}
        self.audience.save()
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_snapshot_stale())

    def test_subtype_change_makes_stale(self):
        self._prepare()
        self.audience.contact_subtype = SUBTYPE_ALL_BUYERS
        self.audience.save()
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_snapshot_stale())

    def test_deactivate_audience_makes_stale(self):
        self._prepare()
        self.audience.is_active = False
        self.audience.save()
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_snapshot_stale())

    def test_reprepare_clears_stale(self):
        self._prepare()
        self.audience.criteria = {'primary_cities': ['Алматы']}
        self.audience.save()
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_snapshot_stale())
        prepare_campaign_snapshot(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_snapshot_stale())


class MarketingCampaignCounterInvariantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        self.audience = make_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_ALL_BUYERS,
        )

    def test_eligible_plus_excluded_equals_unique(self):
        parts_phone = next_phone()
        market_phone = next_phone()
        buyer = make_buyer(phone_normalized=parts_phone)
        grant_consent(buyer)
        Order.objects.create(
            customer_name='Market buyer',
            customer_phone=market_phone,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
            status=Order.STATUS_PAID,
        )
        campaign = MarketingCampaign.objects.create(
            name='Counter campaign',
            audience=self.audience,
            purpose=PURPOSE_PARTS_BUYERS,
            created_by=self.user,
        )
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(
            campaign.eligible_count + campaign.excluded_count,
            campaign.unique_count,
        )

    def test_source_summary_has_no_phone_fields(self):
        buyer = make_buyer()
        grant_consent(buyer)
        campaign = make_campaign(self.audience, self.user)
        prepare_campaign_snapshot(campaign.pk)
        recipient = campaign.recipients.get(phone_normalized=buyer.phone_normalized)
        serialized = str(recipient.source_summary).lower()
        forbidden = ('phone', 'whatsapp', 'token', 'provider')
        for token in forbidden:
            self.assertNotIn(token, serialized)
        self.assertNotIn(buyer.phone_normalized, serialized)
