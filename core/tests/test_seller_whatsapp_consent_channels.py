from __future__ import annotations

import csv
import json
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_SOURCE_REGISTRATION,
    CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
    CONTACT_CONSENT_SOURCE_WHATSAPP,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    SELLER_LINK_WHATSAPP_CONSENT_VERSION,
    SELLER_PLATFORM_CONFIRM_TEMPLATE,
    SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
    SELLER_REGISTRATION_WHATSAPP_CONSENT_VERSION,
    Seller,
    SellerContactConsent,
)
from core.services.seller_identity import create_unified_seller_account
from core.services.seller_whatsapp_consent import (
    USED_OR_STALE_LINK_MESSAGE,
    apply_seller_portal_whatsapp_consent,
    build_seller_whatsapp_consent_token,
    get_seller_whatsapp_marketing_consent,
    set_seller_whatsapp_marketing_consent,
)

REQUEST_PASSWORD = 'RequestPass123'


def make_seller(whatsapp='77015550101', **kwargs) -> Seller:
    defaults = {
        'name': 'Consent seller',
        'whatsapp': whatsapp,
        'city': 'Алматы',
        'transport_type': 'car',
        'is_active': True,
        'is_paused': False,
        'receive_requests': True,
        'is_test_seller': False,
    }
    defaults.update(kwargs)
    return Seller.objects.create(**defaults)


class SellerRegistrationWhatsAppConsentTests(TestCase):
    def _create(self, payload):
        return self.client.post(
            '/api/create-seller/',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _base_payload(self, whatsapp='77015550111', **extra):
        payload = {
            'name': 'New Shop',
            'whatsapp': whatsapp,
            'password': REQUEST_PASSWORD,
            'password_confirm': REQUEST_PASSWORD,
            'transport_type': 'car',
            'city': 'Алматы',
        }
        payload.update(extra)
        return payload

    def test_registration_without_checkbox_does_not_create_granted_consent(self):
        response = self._create(self._base_payload())
        self.assertEqual(response.status_code, 200, response.content)
        seller = Seller.objects.get(whatsapp='77015550111')
        self.assertFalse(seller.receive_requests)
        self.assertFalse(
            SellerContactConsent.objects.filter(
                seller=seller,
                status=CONTACT_CONSENT_STATUS_GRANTED,
            ).exists()
        )

    def test_registration_with_checkbox_grants_consent(self):
        response = self._create(self._base_payload(whatsapp_marketing_consent=True))
        self.assertEqual(response.status_code, 200, response.content)
        seller = Seller.objects.get(whatsapp='77015550111')
        consent = SellerContactConsent.objects.get(seller=seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.source, CONTACT_CONSENT_SOURCE_REGISTRATION)
        self.assertEqual(
            consent.consent_text_version,
            SELLER_REGISTRATION_WHATSAPP_CONSENT_VERSION,
        )
        self.assertEqual(consent.evidence_reference, f'registration:seller:{seller.pk}')
        self.assertEqual(consent.channel, CONTACT_CONSENT_CHANNEL_WHATSAPP)
        self.assertEqual(consent.purpose, CONTACT_CONSENT_PURPOSE_MARKETING)
        self.assertTrue(seller.receive_requests)
        self.assertFalse(seller.is_paused)

    def test_catalog_seller_register_with_checkbox_grants_consent(self):
        response = self.client.post(reverse('seller_register'), {
            'name': 'Catalog Shop',
            'phone': '77015550201',
            'password': 'ShopPass12345',
            'city': 'Алматы',
            'address': 'ул. Регистрации, 1',
            'pickup_same_as_store': 'on',
            'pickup_available': 'on',
            'whatsapp_marketing_consent': 'on',
        })
        self.assertEqual(response.status_code, 302)
        seller = Seller.objects.get(whatsapp='77015550201')
        consent = SellerContactConsent.objects.get(seller=seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.source, CONTACT_CONSENT_SOURCE_REGISTRATION)
        self.assertEqual(
            consent.consent_text_version,
            SELLER_REGISTRATION_WHATSAPP_CONSENT_VERSION,
        )
        self.assertEqual(consent.evidence_reference, f'registration:seller:{seller.pk}')
        self.assertTrue(seller.receive_requests)
        self.assertFalse(seller.is_paused)

    def test_catalog_seller_register_without_checkbox_does_not_grant(self):
        response = self.client.post(reverse('seller_register'), {
            'name': 'Catalog Shop No',
            'phone': '77015550202',
            'password': 'ShopPass12345',
            'city': 'Алматы',
            'address': 'ул. Регистрации, 1',
            'pickup_same_as_store': 'on',
            'pickup_available': 'on',
        })
        self.assertEqual(response.status_code, 302)
        seller = Seller.objects.get(whatsapp='77015550202')
        self.assertFalse(seller.receive_requests)
        self.assertFalse(
            SellerContactConsent.objects.filter(
                seller=seller,
                status=CONTACT_CONSENT_STATUS_GRANTED,
            ).exists()
        )


class SellerWhatsAppConsentHelperTests(TestCase):
    def test_invalid_current_whatsapp_does_not_return_old_consent(self):
        seller = make_seller(whatsapp='77015550301')
        set_seller_whatsapp_marketing_consent(
            seller,
            True,
            source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
            consent_text_version=SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
            evidence_reference='test',
        )
        self.assertIsNotNone(get_seller_whatsapp_marketing_consent(seller))
        seller.whatsapp = 'not-a-phone'
        seller.save(update_fields=['whatsapp'])
        self.assertIsNone(get_seller_whatsapp_marketing_consent(seller))
        self.assertTrue(
            SellerContactConsent.objects.filter(
                seller=seller,
                status=CONTACT_CONSENT_STATUS_GRANTED,
            ).exists()
        )

    def test_grant_then_revoke_keeps_original_consented_at(self):
        seller = make_seller(whatsapp='77015550302')
        granted = set_seller_whatsapp_marketing_consent(
            seller,
            True,
            source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
            consent_text_version=SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
            evidence_reference='test-grant',
        )
        original_consented_at = SellerContactConsent.objects.get(pk=granted.pk).consented_at
        self.assertIsNotNone(original_consented_at)
        revoked = set_seller_whatsapp_marketing_consent(
            seller,
            False,
            source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
            consent_text_version=SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
            evidence_reference='test-revoke',
        )
        self.assertEqual(revoked.pk, granted.pk)
        self.assertEqual(revoked.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.assertEqual(revoked.consented_at, original_consented_at)
        self.assertIsNotNone(revoked.revoked_at)
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user, self.seller, _profile = create_unified_seller_account(
            name='Portal A',
            whatsapp='77015550121',
            password=REQUEST_PASSWORD,
            city='Алматы',
        )
        self.other_user, self.other, _other_profile = create_unified_seller_account(
            name='Portal B',
            whatsapp='77015550122',
            password=REQUEST_PASSWORD,
            city='Алматы',
        )

    def _login(self, whatsapp='77015550121'):
        response = self.client.post(
            '/api/seller-login/',
            data=json.dumps({'whatsapp': whatsapp, 'password': REQUEST_PASSWORD}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.client.get('/request-parts/cabinet/')

    def _headers(self):
        return {
            'HTTP_X_CSRFTOKEN': self.client.cookies['csrftoken'].value,
        }

    def _post(self, action, extra=None):
        payload = {'action': action}
        if extra:
            payload.update(extra)
        return self.client.post(
            reverse('seller_whatsapp_consent_api'),
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
            **self._headers(),
        )

    def test_seller_cannot_change_another_seller_consent(self):
        self._login('77015550121')
        response = self._post('grant', extra={'seller_id': self.other.pk})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(
            SellerContactConsent.objects.filter(
                seller=self.seller,
                status=CONTACT_CONSENT_STATUS_GRANTED,
            ).exists()
        )
        self.assertFalse(SellerContactConsent.objects.filter(seller=self.other).exists())
        self.other.refresh_from_db()
        self.assertFalse(self.other.receive_requests)

    def test_grant_sets_portal_consent_and_unpauses(self):
        self._login()
        response = self._post('grant')
        self.assertEqual(response.status_code, 200, response.content)
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.source, CONTACT_CONSENT_SOURCE_SELLER_PORTAL)
        self.assertEqual(
            consent.consent_text_version,
            SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
        )
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)

    def test_revoke_keeps_seller_active(self):
        self._login()
        self._post('grant')
        response = self._post('revoke')
        self.assertEqual(response.status_code, 200, response.content)
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)
        self.assertTrue(self.seller.is_active)

    def test_repeated_grant_does_not_create_second_consent(self):
        self._login()
        self._post('grant')
        self._post('revoke')
        self._post('grant')
        self.assertEqual(SellerContactConsent.objects.filter(seller=self.seller).count(), 1)

    def test_post_without_csrf_is_forbidden(self):
        self._login()
        response = self.client.post(
            reverse('seller_whatsapp_consent_api'),
            data=json.dumps({'action': 'grant'}),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SellerContactConsent.objects.filter(seller=self.seller).exists())


class SellerSignedLinkWhatsAppConsentTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.seller = make_seller(
            whatsapp='77015550131',
            receive_requests=True,
            is_paused=False,
        )
        self.token = build_seller_whatsapp_consent_token(self.seller)
        self.url = reverse('seller_whatsapp_consent_link', kwargs={'token': self.token})

    def _csrf_post(self, action, url=None):
        get_response = self.client.get(url or self.url)
        return self.client.post(
            url or self.url,
            data={'action': action},
            HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value,
        ), get_response

    def test_valid_get_does_not_change_consent(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'подтверждение WhatsApp')
        self.assertFalse(SellerContactConsent.objects.filter(seller=self.seller).exists())
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)

    def test_yes_post_grants_consent(self):
        response, _get = self._csrf_post('grant')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'включено')
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.source, CONTACT_CONSENT_SOURCE_SELLER_PORTAL)
        self.assertEqual(
            consent.consent_text_version,
            SELLER_LINK_WHATSAPP_CONSENT_VERSION,
        )
        self.assertNotIn(self.token, consent.evidence_reference)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)

    def test_no_post_revokes_consent(self):
        response, _get = self._csrf_post('revoke')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'отключено')
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)

    def test_tampered_token_is_rejected(self):
        bad = self.token[:-1] + ('A' if self.token[-1] != 'A' else 'B')
        response = self.client.get(
            reverse('seller_whatsapp_consent_link', kwargs={'token': bad})
        )
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Ссылка недействительна', status_code=404)
        self.assertNotContains(response, self.seller.name, status_code=404)
        self.assertFalse(SellerContactConsent.objects.exists())

    def test_expired_token_is_rejected(self):
        past = time.time() - (31 * 24 * 60 * 60)
        with patch('django.core.signing.time.time', return_value=past):
            expired = build_seller_whatsapp_consent_token(self.seller)
        response = self.client.get(
            reverse('seller_whatsapp_consent_link', kwargs={'token': expired})
        )
        self.assertEqual(response.status_code, 410)
        self.assertContains(response, 'Ссылка подтверждения устарела', status_code=410)
        self.assertFalse(SellerContactConsent.objects.exists())

    def test_token_phone_must_match_current_seller_whatsapp(self):
        self.seller.whatsapp = '77015550999'
        self.seller.save(update_fields=['whatsapp'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(SellerContactConsent.objects.exists())

    def test_get_query_action_does_not_change_consent(self):
        response = self.client.get(self.url + '?action=grant')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SellerContactConsent.objects.exists())

    def test_signed_link_cannot_be_reused_after_first_decision(self):
        first, _get = self._csrf_post('grant')
        self.assertEqual(first.status_code, 200)
        consent = SellerContactConsent.objects.get(seller=self.seller)
        first_updated_at = consent.updated_at
        second, used_get = self._csrf_post('revoke')
        self.assertEqual(used_get.status_code, 409)
        self.assertContains(
            used_get,
            USED_OR_STALE_LINK_MESSAGE,
            status_code=409,
        )
        self.assertNotContains(used_get, self.seller.name, status_code=409)
        self.assertEqual(second.status_code, 409)
        self.assertContains(second, USED_OR_STALE_LINK_MESSAGE, status_code=409)
        self.assertNotContains(second, self.seller.name, status_code=409)
        consent.refresh_from_db()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.updated_at, first_updated_at)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)

    def test_signed_link_cannot_overwrite_newer_cabinet_consent(self):
        issued = timezone.now() - timedelta(minutes=5)
        with patch(
            'core.services.seller_whatsapp_consent.timezone.now',
            return_value=issued,
        ):
            old_token = build_seller_whatsapp_consent_token(self.seller)
        old_url = reverse('seller_whatsapp_consent_link', kwargs={'token': old_token})
        apply_seller_portal_whatsapp_consent(self.seller, granted=True)
        consent = SellerContactConsent.objects.get(seller=self.seller)
        cabinet_updated_at = consent.updated_at
        response, get_response = self._csrf_post('revoke', url=old_url)
        self.assertEqual(get_response.status_code, 409)
        self.assertContains(
            get_response,
            USED_OR_STALE_LINK_MESSAGE,
            status_code=409,
        )
        self.assertNotContains(get_response, self.seller.name, status_code=409)
        self.assertEqual(response.status_code, 409)
        consent.refresh_from_db()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.source, CONTACT_CONSENT_SOURCE_SELLER_PORTAL)
        self.assertEqual(consent.updated_at, cabinet_updated_at)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)

    def test_signed_link_cannot_overwrite_newer_whatsapp_consent(self):
        issued = timezone.now() - timedelta(minutes=5)
        with patch(
            'core.services.seller_whatsapp_consent.timezone.now',
            return_value=issued,
        ):
            old_token = build_seller_whatsapp_consent_token(self.seller)
        old_url = reverse('seller_whatsapp_consent_link', kwargs={'token': old_token})
        set_seller_whatsapp_marketing_consent(
            self.seller,
            True,
            source=CONTACT_CONSENT_SOURCE_WHATSAPP,
            consent_text_version=SELLER_PLATFORM_CONFIRM_TEMPLATE,
            evidence_reference='whatsapp:wamid.newer',
        )
        consent = SellerContactConsent.objects.get(seller=self.seller)
        whatsapp_updated_at = consent.updated_at
        response, get_response = self._csrf_post('revoke', url=old_url)
        self.assertEqual(get_response.status_code, 409)
        self.assertContains(
            get_response,
            USED_OR_STALE_LINK_MESSAGE,
            status_code=409,
        )
        self.assertEqual(response.status_code, 409)
        consent.refresh_from_db()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.source, CONTACT_CONSENT_SOURCE_WHATSAPP)
        self.assertEqual(consent.updated_at, whatsapp_updated_at)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)

    def test_signed_link_post_without_csrf_is_forbidden(self):
        response = self.client.post(self.url, data={'action': 'grant'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SellerContactConsent.objects.filter(seller=self.seller).exists())
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)

    def test_inactive_seller_link_is_rejected(self):
        self.client.get(self.url)
        self.seller.is_active = False
        self.seller.save(update_fields=['is_active'])
        get_response = self.client.get(self.url)
        self.assertEqual(get_response.status_code, 404)
        self.assertContains(get_response, 'Ссылка недействительна', status_code=404)
        self.assertNotContains(get_response, self.seller.name, status_code=404)
        post_response = self.client.post(
            self.url,
            data={'action': 'grant'},
            HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value,
        )
        self.assertEqual(post_response.status_code, 404)
        self.assertFalse(SellerContactConsent.objects.filter(seller=self.seller).exists())

    def test_test_seller_link_is_rejected(self):
        self.client.get(self.url)
        self.seller.is_test_seller = True
        self.seller.save(update_fields=['is_test_seller'])
        get_response = self.client.get(self.url)
        self.assertEqual(get_response.status_code, 404)
        self.assertContains(get_response, 'Ссылка недействительна', status_code=404)
        self.assertNotContains(get_response, self.seller.name, status_code=404)
        post_response = self.client.post(
            self.url,
            data={'action': 'grant'},
            HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value,
        )
        self.assertEqual(post_response.status_code, 404)
        self.assertFalse(SellerContactConsent.objects.filter(seller=self.seller).exists())


class ExportSellerWhatsAppConsentLinksTests(TestCase):
    def _write_csv(self, **options) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'links.csv'
            call_command('export_seller_whatsapp_consent_links', output=str(path), **options)
            with path.open(encoding='utf-8-sig', newline='') as handle:
                return list(csv.DictReader(handle))

    def test_csv_contains_only_eligible_sellers(self):
        eligible = make_seller(whatsapp='77015550141', name='Eligible')
        make_seller(
            whatsapp='77015550142',
            name='Test',
            is_test_seller=True,
        )
        make_seller(
            whatsapp='77015550143',
            name='Inactive',
            is_active=False,
        )
        make_seller(
            whatsapp='1234567890',
            name='Bad phone',
        )
        granted = make_seller(whatsapp='77015550144', name='Granted')
        set_seller_whatsapp_marketing_consent(
            granted,
            True,
            source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
            consent_text_version=SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
            evidence_reference='test',
        )
        revoked = make_seller(
            whatsapp='77015550145',
            name='Revoked',
            receive_requests=False,
            is_paused=True,
        )
        set_seller_whatsapp_marketing_consent(
            revoked,
            False,
            source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
            consent_text_version=SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
            evidence_reference='test',
        )
        rows = self._write_csv()
        ids = {int(row['seller_id']) for row in rows}
        self.assertEqual(ids, {eligible.pk})
        self.assertEqual(rows[0]['phone_normalized'], '77015550141')
        self.assertTrue(rows[0]['consent_url'].startswith('https://zpt.kz/seller/whatsapp-consent/'))

    def test_include_granted_and_revoked_flags(self):
        granted = make_seller(whatsapp='77015550151', name='Granted')
        set_seller_whatsapp_marketing_consent(
            granted,
            True,
            source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
            consent_text_version=SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
            evidence_reference='test',
        )
        revoked = make_seller(whatsapp='77015550152', name='Revoked')
        set_seller_whatsapp_marketing_consent(
            revoked,
            False,
            source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
            consent_text_version=SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
            evidence_reference='test',
        )
        default_ids = {int(row['seller_id']) for row in self._write_csv()}
        self.assertNotIn(granted.pk, default_ids)
        self.assertNotIn(revoked.pk, default_ids)
        granted_ids = {
            int(row['seller_id'])
            for row in self._write_csv(include_granted=True)
        }
        self.assertIn(granted.pk, granted_ids)
        revoked_ids = {
            int(row['seller_id'])
            for row in self._write_csv(include_revoked=True)
        }
        self.assertIn(revoked.pk, revoked_ids)

    def test_include_revoked_does_not_include_paused_seller_without_revoked_consent(self):
        eligible = make_seller(whatsapp='77015550161', name='Eligible unknown')
        paused = make_seller(
            whatsapp='77015550162',
            name='Paused unknown',
            receive_requests=False,
            is_paused=True,
        )
        revoked = make_seller(
            whatsapp='77015550163',
            name='Revoked paused',
            receive_requests=False,
            is_paused=True,
        )
        set_seller_whatsapp_marketing_consent(
            revoked,
            False,
            source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
            consent_text_version=SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
            evidence_reference='test',
        )
        rows = self._write_csv(include_revoked=True)
        ids = {int(row['seller_id']) for row in rows}
        self.assertIn(eligible.pk, ids)
        self.assertIn(revoked.pk, ids)
        self.assertNotIn(paused.pk, ids)
