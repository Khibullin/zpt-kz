from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import SellerProfile
from core.models import Seller, SellerLead
from core.services.seller_identity import create_unified_seller_account
from core.services.seller_lead_admin_workflow import convert_lead_to_request_seller
from core.services.seller_lead_marketplace_onboarding import (
    build_marketplace_invite_whatsapp_url,
    build_marketplace_registration_url,
    claim_seller_lead_after_registration,
    mark_seller_lead_invited,
)


PASSWORD = 'StrongSellerPass123!'


class SellerLeadMarketplaceOnboardingTests(TestCase):
    def _lead(self, **kwargs):
        defaults = {
            'name': 'Invite Parts',
            'whatsapp': '77015550101',
            'city': 'Алматы',
            'instagram_username': 'invite_parts',
            'website_url': 'https://invite-parts.example',
            'request_seller_transport_type': 'car',
            'lifecycle_status': SellerLead.LIFECYCLE_READY_TO_INVITE,
        }
        defaults.update(kwargs)
        return SellerLead.objects.create(**defaults)

    @override_settings(PUBLIC_BASE_URL='https://zpt.kz')
    def test_registration_url_is_prefilled_from_lead(self):
        lead = self._lead()

        url = build_marketplace_registration_url(lead)

        parsed = urlparse(url)
        self.assertEqual(f'{parsed.scheme}://{parsed.netloc}{parsed.path}', 'https://zpt.kz/seller/register/')
        query = parse_qs(parsed.query)
        self.assertEqual(query['name'], ['Invite Parts'])
        self.assertEqual(query['phone'], ['77015550101'])
        self.assertEqual(query['city'], ['Алматы'])
        self.assertEqual(query['instagram'], ['https://www.instagram.com/invite_parts/'])
        self.assertEqual(query['website'], ['https://invite-parts.example'])

    @override_settings(PUBLIC_BASE_URL='https://zpt.kz')
    def test_invite_is_a_manual_whatsapp_link(self):
        lead = self._lead()

        url = build_marketplace_invite_whatsapp_url(lead)

        self.assertTrue(url.startswith('https://wa.me/77015550101?text='))
        self.assertIn('zpt.kz/seller/register/', url)

    def test_registration_get_prefills_existing_form(self):
        response = self.client.get(reverse('seller_register'), {
            'name': 'Prefilled Shop',
            'phone': '+7 (701) 555-01-02',
            'city': 'Астана',
            'instagram': 'https://instagram.com/prefilled',
            'website': 'https://prefilled.example',
        })

        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.initial['name'], 'Prefilled Shop')
        self.assertEqual(form.initial['phone'], '77015550102')
        self.assertEqual(form.initial['city'], 'Астана')
        self.assertEqual(form.initial['instagram'], 'https://instagram.com/prefilled')
        self.assertEqual(form.initial['website'], 'https://prefilled.example')

    def test_unlinked_request_seller_can_be_claimed_by_unified_registration(self):
        existing = Seller.objects.create(
            name='Discovered Request Seller',
            whatsapp='77015550103',
            city='Алматы',
            transport_type='car',
            receive_requests=False,
        )

        user, seller, profile = create_unified_seller_account(
            name='Registered Shop',
            whatsapp='77015550103',
            password=PASSWORD,
            city='Алматы',
        )

        self.assertEqual(seller.pk, existing.pk)
        self.assertEqual(Seller.objects.filter(whatsapp='77015550103').count(), 1)
        self.assertEqual(seller.user_id, user.pk)
        self.assertEqual(profile.user_id, user.pk)
        self.assertEqual(profile.phone, '77015550103')

    def test_successful_registration_claims_matching_seller_lead(self):
        lead = self._lead(whatsapp='77015550104')
        conversion = convert_lead_to_request_seller(lead)
        self.assertTrue(conversion.seller_id)
        lead.refresh_from_db()
        original_seller_id = lead.request_seller_id

        response = self.client.post(reverse('seller_register'), {
            'name': 'Invite Parts Registered',
            'phone': '77015550104',
            'password': PASSWORD,
            'city': 'Алматы',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('seller_login'))
        lead.refresh_from_db()
        seller = Seller.objects.get(pk=original_seller_id)
        profile = SellerProfile.objects.get(user=seller.user)

        self.assertIsNotNone(seller.user_id)
        self.assertEqual(profile.phone, '77015550104')
        self.assertEqual(lead.request_seller_id, original_seller_id)
        self.assertEqual(lead.status, SellerLead.STATUS_REGISTERED)
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_CLAIMED)
        self.assertEqual(Seller.objects.filter(whatsapp='77015550104').count(), 1)

    def test_new_registration_without_precreated_request_seller_claims_lead(self):
        lead = self._lead(
            whatsapp='77015550105',
            request_seller_transport_type='',
            request_seller=None,
        )

        response = self.client.post(reverse('seller_register'), {
            'name': 'Direct Registered Shop',
            'phone': '77015550105',
            'password': PASSWORD,
            'city': 'Алматы',
        })

        self.assertEqual(response.status_code, 302)
        lead.refresh_from_db()
        self.assertEqual(lead.status, SellerLead.STATUS_REGISTERED)
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_CLAIMED)
        self.assertIsNotNone(lead.request_seller_id)
        self.assertIsNotNone(lead.request_seller.user_id)

    def test_manual_invite_moves_ready_lead_to_invited(self):
        lead = self._lead(
            whatsapp='77015550107',
            lifecycle_status=SellerLead.LIFECYCLE_READY_TO_INVITE,
        )

        changed = mark_seller_lead_invited(lead)

        self.assertTrue(changed)
        lead.refresh_from_db()
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_INVITED)
        self.assertEqual(
            lead.marketplace_invitation_status,
            SellerLead.MARKETPLACE_INVITATION_PLANNED,
        )
        self.assertIsNotNone(lead.marketplace_invitation_planned_at)
        self.assertIsNotNone(lead.reviewed_at)

    def test_manual_invite_is_idempotent(self):
        lead = self._lead(
            whatsapp='77015550108',
            lifecycle_status=SellerLead.LIFECYCLE_READY_TO_INVITE,
        )

        self.assertTrue(mark_seller_lead_invited(lead))
        first_planned_at = lead.marketplace_invitation_planned_at
        self.assertFalse(mark_seller_lead_invited(lead))

        lead.refresh_from_db()
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_INVITED)
        self.assertEqual(lead.marketplace_invitation_planned_at, first_planned_at)

    def test_manual_invite_skips_non_ready_lead(self):
        lead = self._lead(
            whatsapp='77015550109',
            lifecycle_status=SellerLead.LIFECYCLE_CLASSIFIED,
        )

        changed = mark_seller_lead_invited(lead)

        self.assertFalse(changed)
        lead.refresh_from_db()
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_CLASSIFIED)

    def test_manual_invite_skips_invalid_whatsapp(self):
        lead = self._lead(
            whatsapp='',
            lifecycle_status=SellerLead.LIFECYCLE_READY_TO_INVITE,
        )

        changed = mark_seller_lead_invited(lead)

        self.assertFalse(changed)
        lead.refresh_from_db()
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_READY_TO_INVITE)

    def test_claim_does_not_overwrite_conflicting_request_seller(self):
        lead = self._lead(whatsapp='77015550106')
        existing = Seller.objects.create(
            name='Existing Lead Seller',
            whatsapp='77015550999',
            transport_type='car',
        )
        lead.request_seller = existing
        lead.save(update_fields=['request_seller', 'updated_at'])

        user = get_user_model().objects.create_user(
            username='77015550106',
            password=PASSWORD,
        )
        profile = SellerProfile.objects.create(
            user=user,
            name='Other Registered',
            phone='77015550106',
        )
        registered = Seller.objects.create(
            user=user,
            name='Other Registered',
            whatsapp='77015550106',
            transport_type='car',
        )

        result = claim_seller_lead_after_registration(
            phone=profile.phone,
            request_seller=registered,
        )

        self.assertIsNone(result)
        lead.refresh_from_db()
        self.assertEqual(lead.request_seller_id, existing.pk)
        self.assertNotEqual(lead.status, SellerLead.STATUS_REGISTERED)
