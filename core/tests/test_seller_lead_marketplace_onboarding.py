from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import SellerProfile
from core.models import Seller, SellerLead, SellerLeadContactCandidate, SellerLeadEvidence
from core.services.seller_identity import create_unified_seller_account
from core.services.seller_lead_admin_workflow import convert_lead_to_request_seller
from core.services.seller_lead_marketplace_onboarding import (
    build_marketplace_invite_message,
    build_marketplace_invite_whatsapp_url,
    build_marketplace_registration_url,
    claim_seller_lead_after_registration,
    mark_seller_lead_invited,
    mark_seller_lead_whatsapp_unavailable,
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
    def test_registration_url_is_short_and_contains_no_lead_data(self):
        lead = self._lead()

        url = build_marketplace_registration_url(lead)

        parsed = urlparse(url)
        self.assertTrue(parsed.path.startswith('/seller/join/'))
        self.assertEqual(parsed.query, '')
        self.assertNotIn('Invite Parts', url)
        self.assertNotIn('77015550101', url)
        self.assertNotIn('invite_parts', url)
        self.assertLess(len(url), 90)

    @override_settings(PUBLIC_BASE_URL='https://zpt.kz')
    def test_invite_copy_keeps_original_personalized_wording(self):
        lead = self._lead(
            name='Китайские запчасти (@kitaisklad.kz) · Almaty',
            instagram_username='kitaisklad.kz',
        )

        message = build_marketplace_invite_message(lead)

        self.assertIn(
            'Здравствуйте! Приглашаем Китайские запчасти (@kitaisklad.kz) · Almaty подключиться к ZPT.KZ.',
            message,
        )
        self.assertIn(
            'Можно создать кабинет продавца, разместить товары и работать с заявками покупателей.',
            message,
        )
        self.assertIn('zpt.kz/seller/join/', message)

    @override_settings(PUBLIC_BASE_URL='https://zpt.kz')
    def test_official_dealer_gets_business_invitation_copy(self):
        lead = self._lead(
            name='Audi Centre Almaty',
            business_type=SellerLead.BUSINESS_TYPE_DEALER,
        )

        message = build_marketplace_invite_message(lead)

        self.assertIn(
            'ZPT.KZ развивает платформу поиска автозапчастей по Казахстану',
            message,
        )
        self.assertIn('отдел запасных частей Audi Centre Almaty', message)
        self.assertIn('дополнительный спрос покупателей на оригинальные запчасти', message)
        self.assertIn('Подключение на текущем этапе бесплатное', message)
        self.assertIn('zpt.kz/seller/join/', message)
        self.assertNotIn('Можно создать кабинет продавца', message)

    @override_settings(PUBLIC_BASE_URL='https://zpt.kz')
    def test_invite_is_a_manual_whatsapp_link_with_short_join_url(self):
        lead = self._lead()

        url = build_marketplace_invite_whatsapp_url(lead)

        self.assertTrue(url.startswith('https://wa.me/77015550101?text='))
        self.assertIn('zpt.kz/seller/join/', url)
        self.assertNotIn('name%3D', url)
        self.assertNotIn('phone%3D', url)

    def test_join_link_redirects_to_clean_register_and_prefills_from_session(self):
        lead = self._lead()
        join_url = build_marketplace_registration_url(
            lead,
            base_url='http://testserver',
        )
        parsed = urlparse(join_url)

        response = self.client.get(parsed.path)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('seller_register'))
        register = self.client.get(reverse('seller_register'))
        self.assertEqual(register.status_code, 200)
        form = register.context['form']
        self.assertEqual(form.initial['name'], 'Invite Parts')
        self.assertEqual(form.initial['phone'], '77015550101')
        self.assertEqual(form.initial['city'], 'Алматы')
        self.assertEqual(
            form.initial['instagram'],
            'https://www.instagram.com/invite_parts/',
        )
        self.assertEqual(form.initial['website'], 'https://invite-parts.example')

    def test_invalid_join_token_returns_404(self):
        response = self.client.get('/seller/join/not-a-valid-token/')

        self.assertEqual(response.status_code, 404)

    def test_tampered_short_join_token_returns_404(self):
        lead = self._lead()
        join_url = build_marketplace_registration_url(
            lead,
            base_url='http://testserver',
        )
        parsed = urlparse(join_url)
        token = parsed.path.rstrip('/').split('/')[-1]
        replacement = 'A' if token[-1] != 'A' else 'B'
        tampered = token[:-1] + replacement

        response = self.client.get(f'/seller/join/{tampered}/')

        self.assertEqual(response.status_code, 404)

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

    def test_unavailable_whatsapp_is_rejected_and_lead_returns_to_enrichment(self):
        lead = self._lead(
            whatsapp='77015550110',
            lifecycle_status=SellerLead.LIFECYCLE_READY_TO_INVITE,
        )
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77015550110',
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_PENDING,
            source_url='https://example.test/contact',
        )
        evidence = SellerLeadEvidence.objects.create(
            seller_lead=lead,
            field_name='whatsapp',
            value='77015550110',
            normalized_value='77015550110',
            confidence=98,
            extraction_method=SellerLeadEvidence.METHOD_PARSER,
            observed_at=lead.created_at,
            is_selected=True,
        )

        changed = mark_seller_lead_whatsapp_unavailable(lead)

        self.assertTrue(changed)
        lead.refresh_from_db()
        candidate.refresh_from_db()
        evidence.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.normalized_phone, '')
        self.assertEqual(lead.status, SellerLead.STATUS_NO_WHATSAPP)
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_CLASSIFIED)
        self.assertIsNotNone(lead.next_enrichment_at)
        self.assertEqual(candidate.status, SellerLeadContactCandidate.STATUS_REJECTED)
        self.assertFalse(candidate.is_primary)
        self.assertFalse(evidence.is_selected)
        self.assertIn('номер не зарегистрирован', lead.notes)

    def test_unavailable_whatsapp_can_correct_already_invited_lead(self):
        lead = self._lead(
            whatsapp='77015550111',
            lifecycle_status=SellerLead.LIFECYCLE_INVITED,
            marketplace_invitation_status=SellerLead.MARKETPLACE_INVITATION_PLANNED,
            marketplace_invitation_planned_at=timezone.now(),
        )

        changed = mark_seller_lead_whatsapp_unavailable(lead)

        self.assertTrue(changed)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_CLASSIFIED)
        self.assertEqual(
            lead.marketplace_invitation_status,
            SellerLead.MARKETPLACE_INVITATION_NONE,
        )
        self.assertIsNone(lead.marketplace_invitation_planned_at)

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
