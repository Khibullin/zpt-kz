from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    Seller,
    SellerContactConsent,
)
from marketing.models import MarketingCampaignRecipient
from marketing.services.audiences.calculators import calculate_audience
from marketing.services.audiences.constants import (
    GROUP_BUYERS,
    GROUP_SELLERS,
    SUBTYPE_MARKETPLACE_SELLERS,
    SUBTYPE_PARTS_REQUESTS,
    SUBTYPE_REQUEST_SELLERS,
)
from marketing.services.campaigns.constants import (
    ELIGIBILITY_ELIGIBLE,
    PURPOSE_MARKETPLACE_SELLERS,
    PURPOSE_PARTS_BUYERS,
    PURPOSE_REQUEST_SELLERS,
)
from marketing.services.campaigns.live_consent import (
    SKIP_REASON_SELLER_NOT_RECEIVING,
    recheck_live_recipient_consent,
)
from marketing.services.campaigns.preparation import prepare_campaign_snapshot
from marketing.tests.test_marketing_audiences import grant_consent, make_buyer, next_phone
from marketing.tests.test_marketing_campaigns import make_audience, make_campaign


def make_parts_seller(**kwargs) -> Seller:
    defaults = {
        'name': 'Parts seller',
        'whatsapp': next_phone(),
        'city': 'Алматы',
        'transport_type': 'car',
        'is_active': True,
        'is_paused': False,
        'receive_requests': True,
    }
    defaults.update(kwargs)
    return Seller.objects.create(**defaults)


def grant_seller_consent(seller: Seller, status=CONTACT_CONSENT_STATUS_GRANTED) -> SellerContactConsent:
    payload = {
        'seller': seller,
        'phone_normalized': seller.whatsapp,
        'channel': CONTACT_CONSENT_CHANNEL_WHATSAPP,
        'purpose': CONTACT_CONSENT_PURPOSE_MARKETING,
        'status': status,
        'consented_at': timezone.now(),
    }
    if status == CONTACT_CONSENT_STATUS_REVOKED:
        payload['revoked_at'] = timezone.now()
    return SellerContactConsent.objects.create(**payload)


class SellerAudienceConsentTests(TestCase):
    def test_seller_without_consent_is_not_recorded(self):
        make_parts_seller()
        result = calculate_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
            criteria={},
        )
        self.assertEqual(result.eligible_count, 0)
        self.assertGreaterEqual(result.consent_not_recorded_count, 1)
        self.assertEqual(result.preview_rows[0].consent_label, 'Не зафиксировано')

    def test_granted_seller_is_eligible(self):
        seller = make_parts_seller()
        grant_seller_consent(seller)
        result = calculate_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
            criteria={},
        )
        self.assertGreaterEqual(result.eligible_count, 1)
        self.assertGreaterEqual(result.granted_count, 1)
        self.assertEqual(result.preview_rows[0].consent_label, 'Дано')

    def test_revoked_seller_is_consent_revoked(self):
        seller = make_parts_seller(is_paused=True, receive_requests=False)
        grant_seller_consent(seller, CONTACT_CONSENT_STATUS_REVOKED)
        result = calculate_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
            criteria={},
        )
        self.assertEqual(result.eligible_count, 0)
        self.assertGreaterEqual(result.revoked_count, 1)
        self.assertEqual(result.preview_rows[0].consent_label, 'Отозвано')

    def test_buyer_consent_still_works(self):
        buyer = make_buyer()
        grant_consent(buyer)
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={},
        )
        self.assertGreaterEqual(result.eligible_count, 1)

    def test_buyer_consent_does_not_make_seller_eligible(self):
        seller = make_parts_seller()
        buyer = make_buyer(phone_normalized=seller.whatsapp)
        grant_consent(buyer)
        result = calculate_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
            criteria={},
        )
        self.assertEqual(result.eligible_count, 0)
        self.assertGreaterEqual(result.consent_not_recorded_count, 1)

    def test_seller_consent_does_not_make_buyer_eligible(self):
        seller = make_parts_seller()
        grant_seller_consent(seller)
        make_buyer(phone_normalized=seller.whatsapp)
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={},
        )
        self.assertEqual(result.eligible_count, 0)


class SellerLiveRecheckTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)

    def _seller_recipient(
        self,
        seller: Seller,
        *,
        consent_status: str,
        eligibility: str = ELIGIBILITY_ELIGIBLE,
        purpose: str = PURPOSE_REQUEST_SELLERS,
        contact_subtype: str = SUBTYPE_REQUEST_SELLERS,
    ):
        audience = make_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=contact_subtype,
        )
        campaign = make_campaign(
            audience,
            self.user,
            purpose=purpose,
        )
        return MarketingCampaignRecipient.objects.create(
            campaign=campaign,
            phone_normalized=seller.whatsapp,
            display_name=seller.name,
            city=seller.city or '—',
            eligibility_status=eligibility,
            consent_status=consent_status,
        )

    def test_seller_live_recheck_uses_seller_consent(self):
        seller = make_parts_seller()
        grant_seller_consent(seller)
        recipient = self._seller_recipient(seller, consent_status=CONTACT_CONSENT_STATUS_GRANTED)
        ok, reason = recheck_live_recipient_consent(recipient)
        self.assertTrue(ok)
        self.assertEqual(reason, '')

    def test_buyer_consent_does_not_pass_seller_live_recheck(self):
        seller = make_parts_seller()
        buyer = make_buyer(phone_normalized=seller.whatsapp)
        grant_consent(buyer)
        recipient = self._seller_recipient(seller, consent_status='')
        ok, reason = recheck_live_recipient_consent(recipient)
        self.assertFalse(ok)
        self.assertEqual(reason, 'consent_not_granted')

    def test_buyer_live_recheck_still_uses_contact_consent(self):
        buyer = make_buyer()
        grant_consent(buyer)
        audience = make_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
        )
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_PARTS_BUYERS,
        )
        recipient = MarketingCampaignRecipient.objects.create(
            campaign=campaign,
            phone_normalized=buyer.phone_normalized,
            display_name='Buyer',
            eligibility_status=ELIGIBILITY_ELIGIBLE,
            consent_status=CONTACT_CONSENT_STATUS_GRANTED,
        )
        ok, reason = recheck_live_recipient_consent(recipient)
        self.assertTrue(ok)
        self.assertEqual(reason, '')

    def test_seller_consent_does_not_pass_buyer_live_recheck(self):
        seller = make_parts_seller()
        grant_seller_consent(seller)
        buyer = make_buyer(phone_normalized=seller.whatsapp)
        audience = make_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
        )
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_PARTS_BUYERS,
        )
        recipient = MarketingCampaignRecipient.objects.create(
            campaign=campaign,
            phone_normalized=buyer.phone_normalized,
            display_name='Buyer',
            eligibility_status=ELIGIBILITY_ELIGIBLE,
            consent_status='',
        )
        ok, reason = recheck_live_recipient_consent(recipient)
        self.assertFalse(ok)

    def test_campaign_snapshot_uses_seller_consent_status(self):
        seller = make_parts_seller()
        grant_seller_consent(seller)
        audience = make_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
        )
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_REQUEST_SELLERS,
        )
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        recipient = campaign.recipients.get(phone_normalized=seller.whatsapp)
        self.assertEqual(recipient.consent_status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(recipient.consent_status_label, 'Дано')
        self.assertEqual(campaign.eligible_count, 1)

    def test_request_sellers_live_recheck_rejects_paused_or_not_receiving(self):
        for kwargs in (
            {'is_paused': True},
            {'receive_requests': False},
        ):
            with self.subTest(**kwargs):
                seller = make_parts_seller(**kwargs)
                grant_seller_consent(seller)
                recipient = self._seller_recipient(
                    seller,
                    consent_status=CONTACT_CONSENT_STATUS_GRANTED,
                )
                ok, reason = recheck_live_recipient_consent(recipient)
                self.assertFalse(ok)
                self.assertEqual(reason, SKIP_REASON_SELLER_NOT_RECEIVING)

    def test_marketplace_sellers_live_recheck_allows_not_receiving(self):
        seller = make_parts_seller(receive_requests=False, is_paused=True)
        grant_seller_consent(seller)
        recipient = self._seller_recipient(
            seller,
            consent_status=CONTACT_CONSENT_STATUS_GRANTED,
            purpose=PURPOSE_MARKETPLACE_SELLERS,
            contact_subtype=SUBTYPE_MARKETPLACE_SELLERS,
        )
        ok, reason = recheck_live_recipient_consent(recipient)
        self.assertTrue(ok)
        self.assertEqual(reason, '')

