from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (Request, Seller, Match, RequestInteraction, RequestOffer, SellerSocialLink,
                         TelegramRequestPublication, TelegramPublicationAttempt, RequestDispatch)
from core.services import request_connections as service
from core.services.seller_request_access import create_seller_request_access
from core.services.telegram_requests import publish
from core.services.telegram_channel import TelegramChannelError


@override_settings(TWO_WAY_REQUESTS_ENABLED=True, MANUAL_REQUEST_PROCESSING=True, ALLOWED_HOSTS=['testserver'])
class ConnectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='77001112233', password='S3cure-Test-password')
        self.seller = Seller.objects.create(user=self.user, name='Магазин', whatsapp='77001112233',
            transport_type='car', city='Алматы', contact_access_verified=True, direct_contacts_enabled=True,
            sells_new_parts=True, all_brands=True, all_categories=True)
        self.req = Request.objects.create(brand='Mercedes-Benz', model='E400', year=2015, city='Алматы',
            category='Привод', phone='77002223344', vin='WDD12345678901234', description='Закрытые сведения',
            public_summary='Передний наружный ШРУС', seller_feed_enabled=True, transport_type='car',
            contact_consent_at=timezone.now(), public_consent_at=timezone.now(), requested_quantity=2)
        self.detail = reverse('connection_detail', args=[self.req.pk])
        self.buyer = reverse('connection_buyer', args=[self.req.pk, self.req.access_token])
        self.client.force_login(self.user)

    def click(self, channel='whatsapp'):
        return self.client.post(reverse('connection_buyer_contact', args=[self.req.pk, self.req.access_token, self.seller.pk, channel]))

    def offer(self, seller=None, **extra):
        fields = dict(request=self.req, seller=seller or self.seller, article='OEM-123', manufacturer='Завод',
            condition='new', quantity=2, price=10000, delivery_price=1000, delivery_days=2,
            availability='stock', warranty='12 месяцев', fitment_confirmed=True,
            expires_at=timezone.now() + timedelta(days=1))
        fields.update(extra)
        return RequestOffer.objects.create(**fields)

    def test_incomplete_profile_can_view_and_respond_without_dispatch(self):
        self.seller.all_brands = self.seller.all_categories = False
        self.seller.save()
        self.assertContains(self.client.get(reverse('connection_feed')), 'Передний наружный ШРУС')
        self.client.post(self.detail, {'action': 'interest', 'source': 'telegram'})
        self.assertTrue(Match.objects.filter(request=self.req, seller=self.seller).exists())
        self.assertEqual(RequestInteraction.objects.get(kind='interest').source, 'telegram')
        self.assertFalse(RequestDispatch.objects.exists())

    def test_public_summary_never_contains_private_data(self):
        self.client.logout()
        response = self.client.get(self.detail)
        self.assertEqual(response.status_code, 200)
        for value in (self.req.phone, self.req.vin, self.req.description, str(self.req.access_token)):
            self.assertNotContains(response, value)
        self.req.public_summary = 'Звоните +7 700 222 33 44'
        self.req.save()
        self.assertEqual(self.client.get(self.detail).status_code, 404)

    def test_no_public_consent_requires_login(self):
        self.req.public_consent_at = None
        self.req.save()
        self.client.logout()
        self.assertEqual(self.client.get(self.detail).status_code, 302)

    def test_feed_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('connection_feed'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(self.req.public_summary, response.content.decode())

    def test_double_interest_reuses_match_and_access(self):
        first = self.client.post(self.detail, {'action': 'interest'})
        second = self.client.post(self.detail, {'action': 'interest'})
        self.assertEqual(first.url, second.url)
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(self.req.seller_access_links.count(), 1)
        self.assertEqual(RequestInteraction.objects.filter(kind='interest').count(), 1)

    def test_unknown_account_cannot_get_contacts_by_username(self):
        self.client.logout()
        response = self.client.post(self.detail, {'action': 'interest', 'username': 'trusted_seller'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Match.objects.exists())

    def test_new_unverified_seller_can_express_interest_but_cannot_get_contacts(self):
        self.seller.contact_access_verified = False
        self.seller.save()
        response = self.client.post(self.detail, {'action': 'interest'})
        self.assertContains(response, 'Интерес сохранён')
        self.assertNotContains(response, self.req.phone)
        access = self.req.seller_access_links.get()
        self.assertEqual(self.client.get(reverse('seller_request_link', args=[access.token])).status_code, 403)

    def test_seller_page_requires_identity_and_consent(self):
        access = service.participate(self.req, self.seller)
        url = reverse('seller_request_link', args=[access.token])
        self.assertContains(self.client.get(url), self.req.vin)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.user)
        self.req.contact_consent_at = None
        self.req.save()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_other_user_cannot_open_forwarded_link_or_filter(self):
        access = service.participate(self.req, self.seller)
        other = get_user_model().objects.create_user(username='other', password='password')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('seller_request_link', args=[access.token])).status_code, 403)
        self.assertEqual(self.client.get(reverse('seller_request_filters_link', args=[access.token])).status_code, 403)

    def test_forged_legacy_session_cannot_identify_seller(self):
        other = get_user_model().objects.create_user(username='other', password='password')
        self.client.force_login(other)
        session = self.client.session
        session['seller_id'] = self.seller.pk
        session.save()
        self.assertEqual(self.client.post(self.detail, {'action': 'interest'}).status_code, 302)
        self.assertFalse(Match.objects.exists())

    def test_expired_access_and_explicit_interest_renewal(self):
        access = service.participate(self.req, self.seller)
        access.expires_at = timezone.now() - timedelta(seconds=1)
        access.save()
        self.assertEqual(self.client.get(reverse('seller_request_link', args=[access.token])).status_code, 410)
        self.assertEqual(self.client.post(reverse('connection_seller_contact', args=[self.req.pk, 'whatsapp'])).status_code, 403)
        renewed = service.participate(self.req, self.seller)
        self.assertNotEqual(access.pk, renewed.pk)
        self.assertEqual(Match.objects.count(), 1)

    def test_buyer_list_works_without_meta_dispatch(self):
        response = self.client.get(self.buyer)
        self.assertContains(response, 'Магазин')
        self.assertContains(response, 'Написать в WhatsApp')
        self.assertFalse(RequestDispatch.objects.exists())

    def test_click_is_not_contact_or_purchase(self):
        self.assertEqual(self.click().status_code, 302)
        self.assertEqual(self.click().status_code, 302)
        self.assertEqual(RequestInteraction.objects.filter(kind='whatsapp_click').count(), 1)
        self.assertFalse(RequestInteraction.objects.filter(kind='contact_confirmed').exists())
        self.req.refresh_from_db()
        self.assertEqual(self.req.purchase_result, '')

    def test_telegram_and_whatsapp_are_independent_direct_channels(self):
        SellerSocialLink.objects.create(seller=self.seller, kind='telegram', url='https://t.me/real_seller')
        self.assertEqual(self.click('telegram').url, 'https://t.me/real_seller')
        self.req.buyer_telegram = 'real_buyer'
        self.req.save()
        service.participate(self.req, self.seller)
        response = self.client.post(reverse('connection_seller_contact', args=[self.req.pk, 'telegram']))
        self.assertEqual(response.url, 'https://t.me/real_buyer')
        self.assertEqual(RequestInteraction.objects.filter(kind='telegram_click').count(), 2)

    def test_unsafe_telegram_links_rejected(self):
        for value in ['https://evil.test/name', 'https://t.me/+invite', 'https://t.me/s/group', 'some_bot', 'https://t.me/name?start=token', 'https://t.me:broken/name']:
            self.assertEqual(service.telegram_contact(value), '')

    def test_buyer_wrong_token_or_request_id(self):
        self.assertEqual(self.client.get(reverse('connection_buyer', args=[self.req.pk, uuid4()])).status_code, 404)
        self.assertEqual(self.client.get(reverse('connection_buyer', args=[self.req.pk+1, self.req.access_token])).status_code, 404)

    def test_buyer_revocation_and_expiry(self):
        self.req.access_expires_at = timezone.now() - timedelta(seconds=1)
        self.req.save()
        self.assertEqual(self.client.get(self.buyer).status_code, 410)
        self.assertEqual(self.click().status_code, 410)

    def test_csrf_required_for_interest_and_contact(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.detail, {'action': 'interest'}).status_code, 403)
        self.assertEqual(client.post(reverse('connection_buyer_contact', args=[self.req.pk, self.req.access_token, self.seller.pk, 'whatsapp'])).status_code, 403)
        self.assertFalse(RequestInteraction.objects.exists())

    def test_direct_contact_opt_out_and_non_recommended_seller(self):
        self.seller.direct_contacts_enabled = False
        self.seller.save()
        self.assertEqual(self.click().status_code, 404)
        self.assertFalse(Match.objects.exists())

    def test_buyer_confirms_purchase_separately_and_repeats_are_idempotent(self):
        service.participate(self.req, self.seller)
        self.client.post(self.detail, {'action': 'purchase_reported'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.purchase_result, '')
        self.client.post(self.buyer, {'action': 'purchase_confirmed', 'seller': self.seller.pk})
        self.client.post(self.buyer, {'action': 'purchase_confirmed', 'seller': self.seller.pk})
        self.req.refresh_from_db()
        self.assertEqual(self.req.purchase_result, 'purchased')
        self.assertEqual(RequestInteraction.objects.filter(kind='purchase_confirmed').count(), 1)
        self.assertEqual(self.client.post(self.buyer, {'action': 'no_purchase', 'seller': self.seller.pk}).status_code, 409)

    def test_unrelated_seller_cannot_be_used_for_buyer_result(self):
        self.assertEqual(self.client.post(self.buyer, {'action': 'purchase_confirmed', 'seller': self.seller.pk}).status_code, 404)

    def test_offers_sort_by_comparable_total_and_urgent_priority(self):
        self.offer(price=10000, delivery_days=5)
        other = Seller.objects.create(name='Другой', whatsapp='77009998877', city='Алматы',
            direct_contacts_enabled=True, contact_access_verified=True)
        self.offer(other, price=11000, delivery_price=0, delivery_days=1)
        # Same total, earlier arrival wins.
        self.assertEqual(service.recommendations(self.req)[0]['seller'].pk, other.pk)
        self.req.buyer_priority = 'urgent'
        self.req.save()
        self.assertEqual(service.recommendations(self.req)[0]['seller'].pk, other.pk)

    def test_wrong_quantity_original_and_expired_offers_not_confirmed(self):
        offer = self.offer(quantity=1)
        self.assertIsNone(service.recommendations(self.req)[0]['offer'])
        offer.quantity = 2
        offer.save()
        self.req.buyer_priority = 'original'
        self.assertIsNone(service.recommendations(self.req)[0]['offer'])
        offer.is_original = True
        offer.expires_at = timezone.now() - timedelta(seconds=1)
        offer.save()
        self.assertIsNone(service.recommendations(self.req)[0]['offer'])

    def test_unverified_or_unconfirmed_offer_not_promoted(self):
        self.offer()
        self.seller.contact_access_verified = False
        self.seller.save()
        self.assertIsNone(service.recommendations(self.req)[0]['offer'])

    def test_cabinet_no_longer_bypasses_contact_gate(self):
        Match.objects.create(request=self.req, seller=self.seller)
        payload = self.client.get('/api/seller-requests/').json()
        self.assertEqual(payload['requests'][0]['phone'], '')
        self.assertNotIn(self.req.description, str(payload))
        self.assertEqual(payload['requests'][0]['whatsapp_url'], '')

    @override_settings(TWO_WAY_REQUESTS_ENABLED=False)
    def test_rollout_off_is_404(self):
        self.assertEqual(self.client.get(self.detail).status_code, 404)
        self.assertEqual(self.client.get(self.buyer).status_code, 404)

    def test_pages_render_and_no_store(self):
        for url in [self.detail, self.buyer, reverse('connection_quality'), reverse('connection_feed')]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'no-store')
            self.assertIn('noindex', response['X-Robots-Tag'])


@override_settings(TWO_WAY_REQUESTS_ENABLED=True)
class PublicationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='admin')
        self.req = Request.objects.create(phone='77001234567', brand='Mercedes', public_summary='Наружные ШРУСы',
            seller_feed_enabled=True, public_consent_at=timezone.now())
        self.pub = TelegramRequestPublication.objects.create(request=self.req, text='Заявка: наружные ШРУСы', reviewed=True)

    @patch('core.services.telegram_requests._call_api', return_value={'message_id': 8})
    @patch('core.services.telegram_requests.verify_channel')
    def test_publish_and_duplicate_are_safe(self, verify, api):
        self.assertEqual(publish(self.pub.pk, self.user), 8)
        payload = api.call_args.args[1]
        url = payload['reply_markup']['inline_keyboard'][0][0]['url']
        self.assertNotIn(str(self.req.access_token), url)
        self.assertNotIn('/sr/', url)
        with self.assertRaises(ValueError):
            publish(self.pub.pk, self.user)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(TelegramPublicationAttempt.objects.count(), 1)

    @patch('core.services.telegram_requests._call_api', side_effect=TelegramChannelError('timeout'))
    @patch('core.services.telegram_requests.verify_channel')
    def test_uncertain_send_never_auto_retries(self, verify, api):
        with self.assertRaises(ValueError):
            publish(self.pub.pk, self.user)
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.status, 'unknown')
        with self.assertRaises(ValueError):
            publish(self.pub.pk, self.user)
        self.assertEqual(api.call_count, 1)

    @patch('core.services.telegram_requests._call_api')
    def test_missing_consent_and_pii_block_before_network(self, api):
        self.req.public_consent_at = None
        self.req.save()
        with self.assertRaises(ValueError):
            publish(self.pub.pk, self.user)
        self.req.public_consent_at = timezone.now()
        self.req.save()
        self.pub.text = self.req.phone
        self.pub.save()
        with self.assertRaises(ValueError):
            publish(self.pub.pk, self.user)
        api.assert_not_called()

    @patch('core.services.telegram_requests._call_api')
    def test_link_515_is_dry_run_then_idempotent_without_send(self, api):
        req = Request.objects.create(pk=515, phone='77000000000')
        call_command('link_existing_telegram_request', stdout=StringIO())
        self.assertFalse(TelegramRequestPublication.objects.filter(request=req).exists())
        call_command('link_existing_telegram_request', apply=True, stdout=StringIO())
        call_command('link_existing_telegram_request', apply=True, stdout=StringIO())
        self.assertEqual(TelegramRequestPublication.objects.get(request=req).message_id, 5)
        self.assertEqual(TelegramPublicationAttempt.objects.filter(publication__request=req).count(), 1)
        api.assert_not_called()


@override_settings(TWO_WAY_REQUESTS_ENABLED=True, MANUAL_REQUEST_PROCESSING=True, ALLOWED_HOSTS=['testserver'])
class EnrollmentAndConsentTests(TestCase):
    setUp = ConnectionTests.setUp
    def test_short_enrollment_returns_to_request_without_auto_admission(self):
        from django.core.cache import cache
        cache.clear()
        self.client.logout()
        response = self.client.post(reverse('connection_join') + f'?request={self.req.pk}&source=telegram', {
            'name': 'Новый магазин', 'city': 'Алматы', 'phone': '77003334455',
            'password': 'New-Secure-password-726!', 'password_confirm': 'New-Secure-password-726!'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('source=telegram', response.url)
        seller = Seller.objects.get(whatsapp='77003334455')
        self.assertFalse(seller.contact_access_verified)
        self.assertContains(self.client.post(self.detail, {'action': 'interest'}), 'Интерес сохранён')

    def test_existing_unclaimed_card_cannot_be_taken_with_phone(self):
        from core.services.seller_identity import create_unified_seller_account, SellerIdentityError
        Seller.objects.create(name='Импорт', whatsapp='77003334455', transport_type='car', city='Алматы')
        with self.assertRaises(SellerIdentityError):
            create_unified_seller_account(name='Чужой', whatsapp='77003334455', city='Алматы', password='New-Secure-password-726!')
        self.assertFalse(get_user_model().objects.filter(username='77003334455').exists())

    def test_buyer_permission_withdrawal_has_history_and_revokes_contacts(self):
        from core.models import RequestConsentEvent
        access = service.participate(self.req, self.seller)
        response = self.client.post(self.buyer, {'action': 'permissions', 'buyer_priority': 'price', 'requested_quantity': 2})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RequestConsentEvent.objects.filter(request=self.req, granted=False).count(), 2)
        self.assertEqual(self.client.get(reverse('seller_request_link', args=[access.token])).status_code, 403)
        self.client.post(self.buyer, {'action': 'permissions', 'buyer_priority': 'price', 'requested_quantity': 2})
        self.assertEqual(RequestConsentEvent.objects.count(), 2)

    def test_suspended_notifications_do_not_block_feed(self):
        from core.models import BroadcastSettings
        from core.request_dispatch_service import seller_allowed_for_dispatch
        self.seller.notification_state = 'suspended'
        self.seller.receive_requests = True
        self.seller.save()
        self.assertFalse(seller_allowed_for_dispatch(self.seller, BroadcastSettings(mode='live'), self.req))
        self.assertContains(self.client.get(reverse('connection_feed')), self.req.public_summary)
