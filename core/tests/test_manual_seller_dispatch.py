from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    BroadcastSettings,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    Match,
    Request,
    RequestDispatch,
    Seller,
    SellerContactConsent,
    SellerRequestAccess,
)
from core.services.manual_seller_dispatch import (
    confirm_manual_dispatch_sent,
    manual_whatsapp_message,
    prepare_manual_dispatch,
)
from core.services.seller_buyer_request_consent import request_consent_status


@override_settings(PUBLIC_BASE_URL='https://zpt.kz', ALLOWED_HOSTS=['*'])
class ManualSellerDispatchTests(TestCase):
    def setUp(self):
        self.req = Request.objects.create(
            source=Request.SOURCE_HOME_SHORT,
            transport_type='car',
            brand='Mercedes-Benz',
            model='E 400 4MATIC',
            year=2015,
            category='Трансмиссия',
            city='Алматы',
            search_scope='city',
            description='Два новых передних наружных ШРУСа, левый и правый.',
            phone='77477930913',
            status='no_sellers',
        )
        self.seller = Seller.objects.create(
            name='Магазин Mercedes',
            whatsapp='77011013032',
            transport_type='car',
            city='Алматы',
            is_active=True,
            is_paused=False,
            receive_requests=True,
            sells_new_parts=True,
            is_dismantler=False,
            all_brands=True,
            all_categories=True,
        )

    def test_permission_required_before_access_and_dispatch(self):
        with self.assertRaises(ValidationError):
            prepare_manual_dispatch(self.req, self.seller)
        self.assertFalse(RequestDispatch.objects.filter(request=self.req).exists())
        self.assertFalse(SellerRequestAccess.objects.filter(request=self.req).exists())
        self.assertFalse(Match.objects.filter(request=self.req).exists())

    def test_one_off_permission_creates_prepared_link_but_not_seller_history(self):
        dispatch, access = prepare_manual_dispatch(
            self.req,
            self.seller,
            permission_evidence='22.10.2026: телефон, согласие на заявку №515',
        )
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_PREPARED)
        self.assertEqual(dispatch.delivery_channel, RequestDispatch.DELIVERY_MANUAL)
        self.assertIsNone(dispatch.sent_at)
        self.assertFalse(Match.objects.filter(request=self.req).exists())
        self.assertTrue(access.expires_at > timezone.now() + timedelta(hours=71))
        msg = manual_whatsapp_message(self.req, 'https://zpt.kz/sr/example/')
        self.assertIn('https://zpt.kz/sr/example/', msg)
        self.assertNotIn(self.req.phone, msg)
        self.assertIn('Два новых передних наружных ШРУСа', msg)
        again, same_access = prepare_manual_dispatch(
            self.req, self.seller, permission_evidence='22.10.2026: повторно подтверждено'
        )
        self.assertEqual(again.pk, dispatch.pk)
        self.assertEqual(same_access.pk, access.pk)

    def test_confirm_only_after_actual_send_and_idempotent(self):
        dispatch, _ = prepare_manual_dispatch(
            self.req, self.seller,
            permission_evidence='22.10.2026: телефон, разрешено передать заявку',
        )
        confirm_manual_dispatch_sent(dispatch)
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_SENT)
        self.assertIsNotNone(dispatch.sent_at)
        match = Match.objects.get(request=self.req, seller=self.seller)
        self.assertEqual(match.status, 'sent')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'sent')
        confirm_manual_dispatch_sent(dispatch)
        self.assertEqual(Match.objects.filter(request=self.req, seller=self.seller).count(), 1)
        self.assertEqual(RequestDispatch.objects.filter(request=self.req).count(), 1)

    def test_manual_mode_does_not_unblock_meta_sender(self):
        settings = BroadcastSettings.load()
        settings.emergency_stop = True
        settings.save(update_fields=['emergency_stop'])
        prepare_manual_dispatch(
            self.req, self.seller,
            permission_evidence='22.10.2026: разрешено передать разовую заявку',
        )
        settings.refresh_from_db()
        self.assertTrue(settings.emergency_stop)

    def test_city_and_specialization_are_not_bypassed(self):
        self.seller.city = 'Астана'
        self.seller.save(update_fields=['city'])
        with self.assertRaises(ValidationError):
            prepare_manual_dispatch(
                self.req, self.seller,
                permission_evidence='22.10.2026: разрешено по телефону',
            )
        self.seller.city = 'Алматы'
        self.seller.all_categories = False
        self.seller.save(update_fields=['city', 'all_categories'])
        with self.assertRaises(ValidationError):
            prepare_manual_dispatch(
                self.req, self.seller,
                permission_evidence='22.10.2026: разрешено по телефону',
            )

    def test_catalog_only_seller_can_get_one_off_manual_link_without_enrollment(self):
        self.seller.catalog_only = True
        self.seller.save(update_fields=['catalog_only'])
        dispatch, access = prepare_manual_dispatch(self.req, self.seller,
            permission_evidence='26.10.2026: продавец разрешил по телефону')
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_PREPARED)
        self.assertEqual(access.seller_id, self.seller.pk)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.catalog_only)
        self.assertFalse(SellerContactConsent.objects.filter(seller=self.seller).exists())

    def test_buyer_request_subscription_is_distinct_from_marketing(self):
        access = SellerRequestAccess.objects.create(
            request=self.req,
            seller=self.seller,
            token='test-request-optin-unique',
            expires_at=timezone.now() + timedelta(days=3),
        )
        url = reverse('seller_request_link', kwargs={'token': access.token})
        self.assertEqual(self.client.post(url, {'action': 'requests_yes'}).status_code, 200)
        self.assertEqual(request_consent_status(self.seller), CONTACT_CONSENT_STATUS_GRANTED)
        consent = SellerContactConsent.objects.get(
            seller=self.seller, purpose=CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS
        )
        self.assertEqual(consent.channel, CONTACT_CONSENT_CHANNEL_WHATSAPP)
        self.assertIn('access:', consent.evidence_reference)
        self.assertFalse(SellerContactConsent.objects.filter(
            seller=self.seller, purpose=CONTACT_CONSENT_PURPOSE_MARKETING
        ).exists())

        self.assertEqual(self.client.post(url, {'action': 'requests_no'}).status_code, 200)
        self.assertEqual(request_consent_status(self.seller), CONTACT_CONSENT_STATUS_REVOKED)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertFalse(SellerContactConsent.objects.filter(
            seller=self.seller, purpose=CONTACT_CONSENT_PURPOSE_MARKETING
        ).exists())

    def test_subscribed_seller_needs_no_new_permission_for_each_request(self):
        SellerContactConsent.objects.create(
            seller=self.seller, phone_normalized=self.seller.whatsapp,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            consented_at=timezone.now(),
        )
        dispatch, _ = prepare_manual_dispatch(self.req, self.seller)
        self.assertIn('buyer_requests:consent:', dispatch.permission_evidence)

    def test_admin_page_prepares_and_confirms_without_sending_api(self):
        user = get_user_model().objects.create_superuser(
            username='manual-admin', email='admin@example.test', password='AdminSecret123!'
        )
        self.client.force_login(user)
        url = reverse('admin:core_request_manual_dispatch', args=[self.req.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            'action': 'prepare', 'seller_id': self.seller.id,
            'one_time_permission': 'yes',
            'permission_evidence': '22.10.2026: продавец дал согласие по телефону',
        })
        self.assertEqual(response.status_code, 302)
        dispatch = RequestDispatch.objects.get(request=self.req, seller=self.seller)
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_PREPARED)
        self.assertFalse(Match.objects.filter(request=self.req).exists())
        response = self.client.post(url, {
            'action': 'confirm_sent', 'dispatch_id': dispatch.pk, 'did_send': 'yes',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Match.objects.filter(request=self.req, seller=self.seller).exists())

    def test_one_time_assignment_preserves_filters_and_requires_permission(self):
        self.seller.all_categories = False
        self.seller.save(update_fields=['all_categories'])
        with self.assertRaises(ValidationError):
            prepare_manual_dispatch(self.req, self.seller, assignment_reason='По решению владельца для этой заявки')
        dispatch, _ = prepare_manual_dispatch(
            self.req, self.seller,
            assignment_reason='По решению владельца для этой заявки',
            permission_evidence='Разрешение продавца подтверждено владельцем',
        )
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.all_categories)
        self.assertIsNone(self.seller.request_filters)
        self.assertEqual(dispatch.status, RequestDispatch.STATUS_PREPARED)
        self.seller.city = 'Астана'
        self.seller.save(update_fields=['city'])
        with self.assertRaises(ValidationError):
            prepare_manual_dispatch(self.req, self.seller,
                assignment_reason='По решению владельца для этой заявки',
                permission_evidence='Разрешение продавца подтверждено владельцем')

    def test_admin_assignment_requires_reason_and_records_actor(self):
        from django.contrib.admin.models import LogEntry
        user = get_user_model().objects.create_superuser(
            username='assignment-admin', password='test-only-password')
        self.client.force_login(user)
        self.seller.all_categories = False
        self.seller.save(update_fields=['all_categories'])
        url = reverse('admin:core_request_manual_dispatch', args=[self.req.pk])
        payload = {'action': 'prepare', 'seller_id': self.seller.pk,
            'one_time_permission': 'yes', 'one_time_assignment': 'yes',
            'permission_evidence': 'Разрешение продавца подтверждено владельцем'}
        self.client.post(url, payload)
        self.assertFalse(RequestDispatch.objects.filter(request=self.req).exists())
        payload['assignment_reason'] = 'Владелец назначил на одну заявку вручную'
        self.client.post(url, payload)
        self.assertTrue(RequestDispatch.objects.filter(request=self.req).exists())
        log = LogEntry.objects.get(user=user, object_id=str(self.req.pk))
        self.assertIn(payload['assignment_reason'], log.get_change_message())
        self.assertIn(str(self.seller.pk), log.get_change_message())

    @override_settings(USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE='ru')
    def test_confirmation_form_keeps_large_id_machine_readable(self):
        import re
        dispatch, _ = prepare_manual_dispatch(self.req, self.seller,
            permission_evidence='Продавец разрешил разовую заявку по телефону')
        RequestDispatch.objects.filter(pk=dispatch.pk).update(id=16489)
        user = get_user_model().objects.create_superuser(username='large-id-admin', password='test-only-password')
        self.client.force_login(user)
        url = reverse('admin:core_request_manual_dispatch', args=[self.req.pk])
        response = self.client.get(url)
        match = re.search(r'name="dispatch_id" value="([^"]+)"', response.content.decode())
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '16489')
        self.client.post(url, {'action': 'confirm_sent', 'dispatch_id': match.group(1), 'did_send': 'yes'})
        self.assertEqual(RequestDispatch.objects.get(pk=16489).status, RequestDispatch.STATUS_SENT)
