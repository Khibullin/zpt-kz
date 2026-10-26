import json
from datetime import timedelta
from urllib.parse import urlsplit, unquote, parse_qs
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from core.models import Request, Seller, SellerChannelConnection, SellerChannelConsentEvent
from core.services.manual_seller_dispatch import prepare_manual_dispatch, manual_whatsapp_url
from core.services.manual_telegram_invitation import settings_url
from core.services.workflow_notifications import eligible


@override_settings(TWO_WAY_REQUESTS_ENABLED=True, PUBLIC_BASE_URL='https://zpt.kz',
                   TELEGRAM_CONNECTION_WEBHOOK_SECRET='test-hook-secret', ALLOWED_HOSTS=['testserver'])
class ManualTelegramInvitationTests(TestCase):
    def setUp(self):
        self.seller = Seller.objects.create(name='Alan', whatsapp='77001112233', city='Алматы',
            transport_type='car', sells_new_parts=True, all_brands=True, all_categories=True)
        self.req = Request.objects.create(brand='BMW', model='5 Series', city='Алматы',
            transport_type='car', category='Топливная система', description='Форсунки',
            public_summary='Форсунки', seller_feed_enabled=True)
        self.dispatch, self.access = prepare_manual_dispatch(self.req, self.seller,
            permission_evidence='26.10.2026: продавец разрешил прислать заявку')
        self.filters = dict(transport_types=['car'], all_brands=True, all_categories=True)

    def webhook(self, data):
        return self.client.post(reverse('workflow_telegram_webhook'), json.dumps(data),
            content_type='application/json', HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='test-hook-secret')

    def start(self, telegram=123, chat_type='private'):
        return self.webhook({'message': {'chat': {'type': chat_type, 'id': telegram},
            'from': {'id': telegram}, 'text': '/start invite_' + self.access.token}})

    def confirm(self, telegram=123):
        return self.webhook({'callback_query': {'from': {'id': telegram},
            'message': {'chat': {'type': 'private', 'id': telegram}},
            'data': 'subscribe:' + self.access.token}})

    @patch('core.telegram_connection_views._call_api', return_value={'message_id': 1})
    def test_message_and_confirmed_subscription_without_claiming_account(self, api):
        message = unquote(manual_whatsapp_url(self.seller, self.req, self.access))
        self.assertIn('Получайте заявки покупателей автоматически в Telegram.', message)
        self.assertIn('марки, модели автомобилей и города', message)
        self.assertIn('https://zpt.kz/t/', message)
        self.assertNotIn(self.access.token, message)
        self.assertFalse(SellerChannelConnection.objects.exists())
        self.start(chat_type='group')
        self.assertFalse(SellerChannelConnection.objects.exists())
        self.start()
        conn = SellerChannelConnection.objects.get(seller=self.seller)
        self.assertIsNone(conn.telegram_id)
        self.assertFalse(conn.telegram_consent)
        self.confirm(124)
        conn.refresh_from_db(); self.assertIsNone(conn.telegram_id)
        self.confirm(); self.confirm()
        conn.refresh_from_db()
        self.assertEqual(conn.telegram_id, 123)
        self.assertTrue(conn.telegram_consent)
        self.assertFalse(conn.telegram_login_allowed)
        self.assertEqual(SellerChannelConsentEvent.objects.filter(channel='telegram', granted=True).count(), 1)
        self.seller.refresh_from_db()
        self.assertIsNone(self.seller.user_id)
        self.assertFalse(self.seller.contact_access_verified)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(api.call_args.args[0], 'sendMessage')

    @patch('core.telegram_connection_views._call_api', return_value={'message_id': 1})
    def test_existing_connection_not_replaced_and_settings_offered(self, api):
        conn = SellerChannelConnection.objects.create(seller=self.seller, telegram_id=123, telegram_consent=True)
        self.start(124); self.confirm(124)
        conn.refresh_from_db(); self.assertEqual(conn.telegram_id, 123)
        self.start()
        self.assertIn('Настроить фильтр', api.call_args.args[1]['reply_markup']['inline_keyboard'][0][0]['text'])
        self.assertFalse(SellerChannelConsentEvent.objects.exists())
        message = unquote(manual_whatsapp_url(self.seller, self.req, self.access))
        self.assertIn('Telegram уже подключён.', message)

    @patch('core.telegram_connection_views._call_api', return_value={'message_id': 1})
    def test_invalid_expired_revoked_and_paused_invitations_cannot_subscribe(self, api):
        self.start()
        self.seller.is_paused = True; self.seller.save(update_fields=['is_paused'])
        self.confirm()
        self.assertIsNone(SellerChannelConnection.objects.get(seller=self.seller).telegram_id)
        self.seller.is_paused = False; self.seller.save(update_fields=['is_paused'])
        self.access.expires_at = timezone.now() - timedelta(seconds=1); self.access.save()
        self.confirm()
        self.assertFalse(SellerChannelConsentEvent.objects.exists())
        self.assertEqual(self.client.get(reverse('seller_request_filters_link', args=[self.access.token])).status_code, 410)

    @patch('core.telegram_connection_views._call_api', return_value={'message_id': 1})
    def test_scoped_settings_save_cities_filters_and_revoke_without_login(self, api):
        self.start(); self.confirm()
        url = settings_url(self.access, 123)
        path = urlsplit(url).path + '?' + urlsplit(url).query
        self.assertContains(self.client.get(path), 'Города покупателей')
        self.assertEqual(self.client.get(urlsplit(url).path).status_code, 403)
        response = self.client.post(path, {'filters': json.dumps(self.filters),
            'telegram_cities': ['Астана'], 'telegram_notifications': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('notifications=', response.url)
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.request_filters['telegram_cities'], ['Астана'])
        self.assertFalse(eligible(self.req, self.seller, 'telegram'))
        self.req.city = 'Астана'; self.req.search_scope = 'kazakhstan'
        self.assertTrue(eligible(self.req, self.seller, 'telegram'))
        self.req.search_scope = 'city'
        self.assertFalse(eligible(self.req, self.seller, 'telegram'))  # Buyer geography is not expanded.
        self.req.search_scope = 'kazakhstan'; self.req.transport_type = 'truck'
        self.assertFalse(eligible(self.req, self.seller, 'telegram'))
        self.client.post(path, {'filters': json.dumps(self.filters), 'telegram_all_cities': 'on'})
        conn = SellerChannelConnection.objects.get(seller=self.seller)
        self.assertFalse(conn.telegram_consent)
        self.assertTrue(SellerChannelConsentEvent.objects.filter(seller=self.seller, granted=False).exists())
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_forged_or_wrong_shop_settings_ticket_cannot_change_filters(self):
        SellerChannelConnection.objects.create(seller=self.seller, telegram_id=123, telegram_consent=True)
        valid = settings_url(self.access, 123)
        path = urlsplit(valid).path
        ticket = parse_qs(urlsplit(valid).query)['notifications'][0]
        self.assertEqual(self.client.post(path + '?notifications=' + ticket + 'x',
            {'filters': json.dumps(self.filters)}).status_code, 403)
        wrong = settings_url(self.access, 124)
        self.assertEqual(self.client.get(urlsplit(wrong).path + '?' + urlsplit(wrong).query).status_code, 403)
        self.seller.refresh_from_db(); self.assertIsNone(self.seller.request_filters)

    @patch('core.telegram_connection_views._call_api', return_value={'message_id': 1})
    def test_notification_invitation_cannot_become_account_login(self, api):
        from django.contrib.auth import get_user_model
        from core.models import TelegramSellerLogin
        user = get_user_model().objects.create_user(username='owner')
        self.seller.user = user
        self.seller.contact_access_verified = True
        self.seller.save(update_fields=['user', 'contact_access_verified'])
        self.start(); self.confirm()
        ticket = TelegramSellerLogin.objects.create(expires_at=timezone.now()+timedelta(minutes=10))
        api.reset_mock()
        self.webhook({'message': {'chat': {'type': 'private', 'id': 123}, 'from': {'id': 123},
            'text': '/start login_' + ticket.pk.hex}})
        self.webhook({'callback_query': {'from': {'id': 123}, 'message': {'chat': {'type': 'private', 'id': 123}},
            'data': 'login:' + ticket.pk.hex}})
        ticket.refresh_from_db()
        self.assertIsNone(ticket.seller_id)
        api.assert_not_called()

    def test_approved_request_page_offers_personal_telegram_without_whatsapp_subscription(self):
        self.req.contact_consent_at = timezone.now()
        self.req.phone = '77019990011'
        self.req.save(update_fields=['contact_consent_at', 'phone'])
        url = reverse('seller_request_link', args=[self.access.token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ответить в WhatsApp')
        self.assertContains(response, 'Подходящие заявки в Telegram')
        self.assertContains(response, 'Подключить Telegram')
        self.assertContains(response, 'https://t.me/zpt_kz_zakaz_bot?start=invite_' + self.access.token)
        self.assertNotContains(response, 'Получать будущие заявки покупателей в WhatsApp?')
        self.assertNotContains(response, 'data-sr-request-subscription')
        self.assertNotContains(response, 'sr-consent-modal')
        self.assertFalse(SellerChannelConsentEvent.objects.exists())
        SellerChannelConnection.objects.create(seller=self.seller, telegram_id=123, telegram_consent=True)
        response = self.client.get(url)
        self.assertContains(response, 'Настроить фильтр заявок')
        self.assertNotContains(response, '>Подключить Telegram<')

    @patch('core.telegram_connection_views._call_api', return_value={'message_id': 1})
    def test_catalog_invitation_requires_explicit_subscription_and_never_claims_store(self, api):
        self.seller.catalog_only = True
        self.seller.save(update_fields=['catalog_only'])
        self.start()
        conn = SellerChannelConnection.objects.get(seller=self.seller)
        self.assertFalse(conn.telegram_consent)
        self.assertIsNone(conn.telegram_id)
        self.confirm()
        conn.refresh_from_db()
        self.assertTrue(conn.telegram_consent)
        self.assertFalse(conn.telegram_login_allowed)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.catalog_only)
        self.assertIsNone(self.seller.user_id)

    def test_short_links_are_private_reusable_scoped_and_expire(self):
        from core.services.manual_short_links import short_url
        request_url = short_url(self.access)
        telegram_url = short_url(self.access, telegram=True)
        self.assertEqual(short_url(self.access), request_url)
        self.assertEqual(len(self.access.short_code),16)
        self.assertNotIn(self.access.token, request_url)
        self.assertEqual(self.client.get(request_url).url, reverse('seller_request_link', args=[self.access.token]))
        response = self.client.get(telegram_url)
        self.assertEqual(response.url, 'https://t.me/zpt_kz_zakaz_bot?start=invite_' + self.access.token)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertFalse(SellerChannelConnection.objects.exists())
        self.req.access_revoked=True; self.req.save(update_fields=['access_revoked'])
        self.assertEqual(self.client.get(request_url).status_code,403)
        self.assertEqual(self.client.get(telegram_url).status_code,403)
        self.access.expires_at=timezone.now()-timedelta(seconds=1); self.access.save()
        self.assertEqual(self.client.get(request_url).status_code,410)
        self.assertEqual(self.client.get('/s/no-such-code/').status_code,404)
