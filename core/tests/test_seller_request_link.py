from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_SOURCE_SELLER_REQUEST_PAGE,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    Match,
    Request,
    Seller,
    SellerContactConsent,
    SellerRequestPageEvent,
)
from core.services.seller_request_access import (
    build_seller_request_access_url,
    create_seller_request_access,
)
from core.services.seller_request_page_events import (
    EVENT_CALL_CLICK,
    EVENT_CANNOT_FULFILL,
    EVENT_CONSENT_NO,
    EVENT_CONSENT_YES,
    EVENT_OUT_OF_STOCK,
    EVENT_PAGE_OPEN,
    EVENT_WHATSAPP_CLICK,
    seller_match_status_label,
)


def _make_request() -> Request:
    return Request.objects.create(
        transport_type='car',
        brand='SecretBrandXYZ',
        model='SecretModelXYZ',
        category='SecretCategoryXYZ',
        city='SecretCityXYZ',
        phone='77019990011',
        description='VIN SECRET123456789 buyer comment',
        status='sent',
    )


def _make_seller(**kwargs) -> Seller:
    defaults = {
        'name': 'Seller on request page',
        'whatsapp': '77015550101',
        'transport_type': 'car',
        'city': 'Алматы',
        'is_active': True,
        'is_paused': False,
        'receive_requests': True,
    }
    defaults.update(kwargs)
    return Seller.objects.create(**defaults)


@override_settings(PUBLIC_BASE_URL='https://zpt.kz', ALLOWED_HOSTS=['*'])
class SellerRequestLinkTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST='zpt.kz')
        self.request_obj = _make_request()
        self.seller = _make_seller()
        self.access = create_seller_request_access(
            request=self.request_obj,
            seller=self.seller,
        )
        self.url = reverse('seller_request_link', kwargs={'token': self.access.token})

    def test_valid_token_returns_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Страница заявки')
        self.assertContains(response, f'Заявка №{self.request_obj.pk}')

    def test_valid_page_shows_request_summary(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'SecretBrandXYZ')
        self.assertContains(response, 'SecretModelXYZ')
        self.assertContains(response, 'SecretCategoryXYZ')
        self.assertContains(response, 'SecretCityXYZ')
        self.assertContains(response, 'VIN SECRET123456789 buyer comment')
        self.assertContains(response, 'Написать покупателю в WhatsApp')
        self.assertContains(response, 'Позвонить покупателю')
        self.assertContains(response, 'https://wa.me/77019990011')
        self.assertContains(response, 'tel:+77019990011')
        self.assertContains(response, 'Нет в наличии')
        self.assertContains(response, 'Не могу выполнить заявку')

    def test_unknown_token_is_not_500(self):
        url = reverse('seller_request_link', kwargs={'token': 'unknown-token-value'})
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 500)
        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            'Ссылка недействительна или больше не доступна.',
            status_code=404,
        )
        self.assertNotContains(response, 'Страница не найдена', status_code=404)

    def test_expired_token_is_not_valid_page(self):
        self.access.expires_at = timezone.now() - timedelta(minutes=1)
        self.access.save(update_fields=['expires_at'])
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(response.status_code, 410)
        self.assertContains(response, 'Срок действия ссылки истёк.', status_code=410)

    def test_page_has_noindex(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'noindex, nofollow')
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertNotContains(response, 'rel="canonical"')

    def test_create_does_not_accept_caller_token(self):
        with self.assertRaises(TypeError):
            create_seller_request_access(
                request=self.request_obj,
                token='weak-token',
            )

    def test_invalid_and_expired_do_not_expose_request_fields(self):
        unknown = self.client.get(
            reverse('seller_request_link', kwargs={'token': 'unknown-token-value'})
        )
        self.access.expires_at = timezone.now() - timedelta(minutes=1)
        self.access.save(update_fields=['expires_at'])
        expired = self.client.get(self.url)
        for response, status in ((unknown, 404), (expired, 410)):
            body = response.content.decode('utf-8')
            self.assertEqual(response.status_code, status)
            self.assertNotIn(self.request_obj.phone, body)
            self.assertNotIn(self.request_obj.brand, body)
            self.assertNotIn(self.request_obj.model, body)
            self.assertNotIn(self.request_obj.category, body)
            self.assertNotIn(self.request_obj.city, body)
            self.assertNotIn(self.request_obj.description, body)
            self.assertNotIn('SECRET123456789', body)
            self.assertNotIn(str(self.request_obj.access_token), body)
            self.assertNotIn(self.access.token, body)

    def test_valid_page_does_not_expose_tokens(self):
        response = self.client.get(self.url)
        body = response.content.decode('utf-8')
        self.assertNotIn(self.access.token, body)
        self.assertNotIn(str(self.request_obj.access_token), body)
        self.assertNotIn(
            f'/r/{self.request_obj.pk}/{self.request_obj.short_token}/',
            body,
        )

    def test_page_without_seller_skips_consent_and_extra_actions(self):
        access = create_seller_request_access(request=self.request_obj)
        url = reverse('seller_request_link', kwargs={'token': access.token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SecretBrandXYZ')
        self.assertContains(response, 'data-needs-consent="0"')
        self.assertNotContains(response, 'Нет в наличии')
        self.assertContains(response, 'https://wa.me/77019990011')

    def test_consent_prompt_shown_when_unknown(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Получать выгодные предложения ZPT.KZ в WhatsApp?')
        self.assertContains(response, 'Да, получать')
        self.assertContains(response, 'Нет, только заявки')
        self.assertContains(response, 'data-needs-consent="1"')

    def test_consent_prompt_hidden_when_already_decided(self):
        now = timezone.now()
        SellerContactConsent.objects.create(
            seller=self.seller,
            phone_normalized=self.seller.whatsapp,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            consented_at=now,
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'data-needs-consent="0"')
        self.assertNotContains(response, 'Да, получать')
        self.assertNotContains(response, 'Нет, только заявки')

    def test_post_yes_grants_marketing_and_keeps_requests(self):
        response = self.client.post(self.url, {'action': 'consent_yes'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertFalse(payload['needs_consent'])
        self.assertIn('https://wa.me/77019990011', payload['whatsapp_url'])
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.source, CONTACT_CONSENT_SOURCE_SELLER_REQUEST_PAGE)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)

    def test_post_no_records_marketing_decline_without_pausing_requests(self):
        response = self.client.post(self.url, {'action': 'consent_no'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)

    def test_post_unavailable_sets_outcome_without_login(self):
        Match.objects.create(
            request=self.request_obj,
            seller=self.seller,
            status='sent',
        )
        response = self.client.post(self.url, {'action': 'unavailable'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['outcome'], EVENT_OUT_OF_STOCK)
        match = Match.objects.get(request=self.request_obj, seller=self.seller)
        self.assertEqual(match.status, 'sent')
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                request=self.request_obj,
                seller=self.seller,
                event_type=EVENT_OUT_OF_STOCK,
            ).count(),
            1,
        )

    def test_post_decline_sets_outcome_without_login(self):
        response = self.client.post(self.url, {'action': 'decline'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['outcome'], EVENT_CANNOT_FULFILL)
        self.assertFalse(
            Match.objects.filter(request=self.request_obj, seller=self.seller).exists()
        )

    def test_same_outcome_is_idempotent(self):
        first = self.client.post(self.url, {'action': 'unavailable'})
        second = self.client.post(self.url, {'action': 'unavailable'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()['ok'])
        self.assertEqual(second.json()['outcome'], EVENT_OUT_OF_STOCK)
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                request=self.request_obj,
                seller=self.seller,
                event_type=EVENT_OUT_OF_STOCK,
            ).count(),
            1,
        )

    def test_other_outcome_does_not_overwrite_first(self):
        first = self.client.post(self.url, {'action': 'unavailable'})
        second = self.client.post(self.url, {'action': 'decline'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        payload = second.json()
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['outcome'], EVENT_OUT_OF_STOCK)
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                request=self.request_obj,
                seller=self.seller,
                event_type__in={EVENT_OUT_OF_STOCK, EVENT_CANNOT_FULFILL},
            ).count(),
            1,
        )

    def test_competing_access_tokens_do_not_duplicate_outcome(self):
        other_access = create_seller_request_access(
            request=self.request_obj,
            seller=self.seller,
        )
        other_url = reverse('seller_request_link', kwargs={'token': other_access.token})
        first = self.client.post(self.url, {'action': 'unavailable'})
        second = self.client.post(other_url, {'action': 'decline'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                request=self.request_obj,
                seller=self.seller,
                event_type__in={EVENT_OUT_OF_STOCK, EVENT_CANNOT_FULFILL},
            ).count(),
            1,
        )

    def test_page_open_is_recorded_once_per_access(self):
        self.client.get(self.url)
        self.client.get(self.url)
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                access=self.access,
                event_type=EVENT_PAGE_OPEN,
            ).count(),
            1,
        )
        event = SellerRequestPageEvent.objects.get(
            access=self.access,
            event_type=EVENT_PAGE_OPEN,
        )
        self.assertEqual(event.request_id, self.request_obj.pk)
        self.assertEqual(event.seller_id, self.seller.pk)
        self.assertNotEqual(event.created_at, None)
        self.assertFalse(hasattr(event, 'token'))

    def test_head_does_not_record_page_open(self):
        self.client.head(self.url)
        self.assertEqual(SellerRequestPageEvent.objects.count(), 0)

    def test_whatsapp_click_is_recorded(self):
        response = self.client.post(self.url, {'action': 'whatsapp_click'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertIn('https://wa.me/77019990011', payload['whatsapp_url'])
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                access=self.access,
                event_type=EVENT_WHATSAPP_CLICK,
            ).count(),
            1,
        )
        self.client.post(self.url, {'action': 'whatsapp_click'})
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                access=self.access,
                event_type=EVENT_WHATSAPP_CLICK,
            ).count(),
            1,
        )

    def test_call_click_is_recorded(self):
        response = self.client.post(self.url, {'action': 'call_click'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['tel_url'], 'tel:+77019990011')
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                access=self.access,
                event_type=EVENT_CALL_CLICK,
            ).count(),
            1,
        )

    def test_repeat_consent_yes_is_idempotent(self):
        first = self.client.post(self.url, {'action': 'consent_yes'})
        second = self.client.post(self.url, {'action': 'consent_yes'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()['ok'])
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                access=self.access,
                event_type=EVENT_CONSENT_YES,
            ).count(),
            1,
        )
        self.assertFalse(
            SellerRequestPageEvent.objects.filter(
                access=self.access,
                event_type=EVENT_CONSENT_NO,
            ).exists()
        )
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)

    def test_repeat_consent_no_is_idempotent(self):
        first = self.client.post(self.url, {'action': 'consent_no'})
        second = self.client.post(self.url, {'action': 'consent_no'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()['ok'])
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                access=self.access,
                event_type=EVENT_CONSENT_NO,
            ).count(),
            1,
        )
        self.assertFalse(
            SellerRequestPageEvent.objects.filter(
                access=self.access,
                event_type=EVENT_CONSENT_YES,
            ).exists()
        )
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)

    def test_consent_yes_then_no_keeps_first_choice(self):
        first = self.client.post(self.url, {'action': 'consent_yes'})
        second = self.client.post(self.url, {'action': 'consent_no'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        payload = second.json()
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['consent'], EVENT_CONSENT_YES)
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(
            list(
                SellerRequestPageEvent.objects.filter(
                    access=self.access,
                    event_type__in={EVENT_CONSENT_YES, EVENT_CONSENT_NO},
                ).values_list('event_type', flat=True)
            ),
            [EVENT_CONSENT_YES],
        )
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)
        self.assertTrue(self.seller.is_active)

    def test_consent_no_then_yes_keeps_first_choice(self):
        first = self.client.post(self.url, {'action': 'consent_no'})
        second = self.client.post(self.url, {'action': 'consent_yes'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        payload = second.json()
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['consent'], EVENT_CONSENT_NO)
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.assertEqual(
            list(
                SellerRequestPageEvent.objects.filter(
                    access=self.access,
                    event_type__in={EVENT_CONSENT_YES, EVENT_CONSENT_NO},
                ).values_list('event_type', flat=True)
            ),
            [EVENT_CONSENT_NO],
        )
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)
        self.assertTrue(self.seller.is_active)

    def test_missing_seller_is_safe_and_skips_seller_actions(self):
        access = create_seller_request_access(request=self.request_obj)
        url = reverse('seller_request_link', kwargs={'token': access.token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SecretBrandXYZ')
        self.assertEqual(SellerRequestPageEvent.objects.filter(access=access).count(), 0)

        consent = self.client.post(url, {'action': 'consent_yes'})
        self.assertEqual(consent.status_code, 400)
        self.assertFalse(SellerContactConsent.objects.filter(seller=self.seller).exists())

        outcome = self.client.post(url, {'action': 'unavailable'})
        self.assertEqual(outcome.status_code, 400)

        whatsapp = self.client.post(url, {'action': 'whatsapp_click'})
        self.assertEqual(whatsapp.status_code, 200)
        self.assertTrue(whatsapp.json()['ok'])
        self.assertEqual(SellerRequestPageEvent.objects.count(), 0)
        self.assertNotIn(self.access.token, whatsapp.content.decode('utf-8'))

    def test_invalid_and_expired_do_not_create_events(self):
        unknown = self.client.get(
            reverse('seller_request_link', kwargs={'token': 'unknown-token-value'})
        )
        self.client.post(
            reverse('seller_request_link', kwargs={'token': 'unknown-token-value'}),
            {'action': 'whatsapp_click'},
        )
        self.access.expires_at = timezone.now() - timedelta(minutes=1)
        self.access.save(update_fields=['expires_at'])
        expired = self.client.get(self.url)
        self.client.post(self.url, {'action': 'call_click'})
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(expired.status_code, 410)
        self.assertEqual(SellerRequestPageEvent.objects.count(), 0)

    def test_cabinet_status_labels(self):
        self.assertEqual(seller_match_status_label('sent'), 'Отправлена')
        self.assertEqual(seller_match_status_label('unavailable'), 'Нет в наличии')
        self.assertEqual(seller_match_status_label('out_of_stock'), 'Нет в наличии')
        self.assertEqual(seller_match_status_label('declined'), 'Не могу выполнить заявку')
        self.assertEqual(seller_match_status_label('cannot_fulfill'), 'Не могу выполнить заявку')
        self.assertEqual(seller_match_status_label('done'), 'Закрыта')

    def test_post_expired_token_is_410(self):
        self.access.expires_at = timezone.now() - timedelta(minutes=1)
        self.access.save(update_fields=['expires_at'])
        response = self.client.post(self.url, {'action': 'consent_yes'})
        self.assertEqual(response.status_code, 410)
        self.assertFalse(response.json()['ok'])

    def test_token_lookup_is_case_sensitive(self):
        mixed = self.access.token
        flipped = mixed.swapcase()
        if flipped == mixed:
            self.skipTest('token has no letters to flip case')
        url = reverse('seller_request_link', kwargs={'token': flipped})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_named_url_reverse(self):
        self.assertEqual(self.url, f'/sr/{self.access.token}/')
        self.assertEqual(
            build_seller_request_access_url(self.access),
            f'https://zpt.kz/sr/{self.access.token}/',
        )

    def test_get_without_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_default_ttl_is_two_hours(self):
        delta = self.access.expires_at - self.access.created_at
        self.assertGreaterEqual(delta.total_seconds(), 2 * 3600 - 5)
        self.assertLessEqual(delta.total_seconds(), 2 * 3600 + 5)

    def test_require_binding_rejects_unbound_access(self):
        with self.assertRaises(ValueError):
            create_seller_request_access(
                request=self.request_obj,
                require_binding=True,
            )
        with self.assertRaises(ValueError):
            create_seller_request_access(
                seller=self.seller,
                require_binding=True,
            )


@override_settings(PUBLIC_BASE_URL='https://zpt.kz', ALLOWED_HOSTS=['*'])
class SellerRequestLinkCsrfTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True, HTTP_HOST='zpt.kz')
        self.request_obj = _make_request()
        self.seller = _make_seller()
        self.access = create_seller_request_access(
            request=self.request_obj,
            seller=self.seller,
        )
        self.url = reverse('seller_request_link', kwargs={'token': self.access.token})

    def test_post_without_csrf_is_rejected_and_does_not_change_data(self):
        response = self.client.post(self.url, {'action': 'whatsapp_click'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SellerRequestPageEvent.objects.count(), 0)
        self.assertFalse(
            SellerContactConsent.objects.filter(seller=self.seller).exists()
        )

    def test_post_with_valid_csrf_is_accepted(self):
        self.client.get(self.url)
        csrf = self.client.cookies['csrftoken'].value
        response = self.client.post(
            self.url,
            {'action': 'whatsapp_click', 'csrfmiddlewaretoken': csrf},
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(
                access=self.access,
                event_type=EVENT_WHATSAPP_CLICK,
            ).count(),
            1,
        )

    def test_rejected_csrf_does_not_record_click_after_page_open(self):
        self.client.get(self.url)
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(event_type=EVENT_PAGE_OPEN).count(),
            1,
        )
        response = self.client.post(self.url, {'action': 'whatsapp_click'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            SellerRequestPageEvent.objects.filter(event_type=EVENT_WHATSAPP_CLICK).count(),
            0,
        )
        self.assertEqual(SellerRequestPageEvent.objects.count(), 1)
