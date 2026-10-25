from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone
from core.models import (Request, Seller, Match, RequestInteraction, SellerAccessClaim,
    RequestOutcomeEvent, RequestSellerReview, SellerChannelConnection, RequestSellerNotification)
from core.services.request_connections import participate
from core.services.workflow_notifications import send_one
from core.services.telegram_channel import TelegramChannelError


@override_settings(TWO_WAY_REQUESTS_ENABLED=True, ALLOWED_HOSTS=['testserver'], TELEGRAM_CONNECTION_WEBHOOK_SECRET='test-hook-secret')
class ApprovedWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='seller-test', password='test-secret')
        self.seller = Seller.objects.create(user=self.user, name='Магазин', whatsapp='77001112233', city='Алматы',
            contact_access_verified=True, direct_contacts_enabled=True, transport_type='car', sells_new_parts=True,
            all_brands=True, all_categories=True)
        self.req = Request.objects.create(brand='Mercedes-Benz', model='E400', city='Алматы', phone='77002223344',
            vin='WDD12345678901234', description='Нужен ШРУС', public_summary='Новый ШРУС', seller_feed_enabled=True,
            public_consent_at=timezone.now(), contact_consent_at=timezone.now())
        self.staff = get_user_model().objects.create_superuser(username='staff', email='staff@example.com', password='test-secret')
    def buyer(self, step):
        return reverse('workflow_buyer_step', args=[self.req.pk, self.req.access_token, step])
    def test_buyer_cards_show_saved_address_map_and_real_review_state(self):
        self.req.transport_type = 'car'
        self.req.save(update_fields=['transport_type'])
        from core.models import SellerExternalCard
        self.seller.address = 'ул. Проверочная, 12'
        self.seller.save(update_fields=['address'])
        SellerExternalCard.objects.create(seller=self.seller, source='two_gis',
            external_id='700000010000001', card_url='https://2gis.kz/almaty/firm/700000010000001')
        response = self.client.get(self.buyer('sellers'))
        for value in ('ул. Проверочная, 12', 'Посмотреть в 2ГИС', 'Пока нет отзывов',
                      'Оставить отзыв', 'О продавце', 'Написать продавцу',
                      'https://2gis.kz/almaty/firm/700000010000001'):
            self.assertContains(response, value)
        self.assertNotContains(response, 'Специализацию уточните')
        self.assertNotContains(response, 'store-icon')
        reviewed = Request.objects.create(purchase_result='purchased')
        RequestSellerReview.objects.create(request=reviewed, seller=self.seller, rating=4,
                                          public_consent=True, moderated=True)
        private = Request.objects.create(purchase_result='purchased')
        RequestSellerReview.objects.create(request=private, seller=self.seller, rating=1,
                                          public_consent=False, moderated=True)
        response = self.client.get(self.buyer('sellers'))
        self.assertContains(response, 'Отзывы: 1')
        self.assertNotContains(response, 'Пока нет отзывов')
        self.req.refresh_from_db()
        self.assertEqual(self.req.purchase_result, '')

    def test_contacted_directory_seller_can_be_reviewed_after_confirmed_purchase(self):
        shop = Seller.objects.create(name='Авторазбор из каталога', city='Алматы')
        RequestInteraction.objects.create(request=self.req, seller=shop, actor='buyer', kind='whatsapp_click', source='public_directory')
        self.assertFalse(Match.objects.filter(request=self.req, seller=shop).exists())
        self.assertEqual(self.client.post(self.buyer('result'), {'result': 'purchased'}).status_code, 302)
        self.assertEqual(self.client.post(self.buyer('seller'), {'seller': str(shop.pk)}).status_code, 302)
        response = self.client.post(self.buyer('review'), {'rating': 5, 'text': 'Спасибо', 'public_consent': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RequestSellerReview.objects.get(request=self.req).seller, shop)
        self.assertFalse(Match.objects.filter(request=self.req, seller=shop).exists())

    def test_dismantler_cards_use_same_design_and_real_coordinates(self):
        self.req.transport_type = 'car'
        self.req.save(update_fields=['transport_type'])
        self.req.is_dismantler_request = True
        self.req.save(update_fields=['is_dismantler_request'])
        self.seller.is_dismantler = True
        self.seller.latitude, self.seller.longitude = '43.238949', '76.889709'
        self.seller.save()
        response = self.client.get(self.buyer('sellers'))
        self.assertContains(response, 'Авторазборы по вашей заявке')
        self.assertContains(response, 'Посмотреть в 2ГИС')
        self.assertContains(response, '76.889709,43.238949')
        self.assertContains(response, 'Написать продавцу')
        self.assertNotContains(response, 'Примерная')

    def test_buyer_screens_and_no_mandatory_seller_for_purchase(self):
        self.req.year = 2001
        self.req.save(update_fields=['year'])
        for step in ('accepted', 'sellers', 'edit', 'result', 'search', 'close', 'done'):
            self.assertEqual(self.client.get(self.buyer(step)).status_code, 200, step)
        self.assertContains(self.client.get(self.buyer('accepted')), ' · 2001')
        self.assertEqual(self.client.post(self.buyer('result'), {'result': 'purchased'}).status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.purchase_result, 'purchased')
        self.assertFalse(RequestInteraction.objects.filter(kind='purchase_confirmed').exists())
        self.assertEqual(self.client.post(self.buyer('seller'), {'seller': 'skip'}).status_code, 302)
    def test_attribution_rejects_other_request_seller(self):
        self.req.purchase_result = 'purchased'; self.req.save()
        self.assertEqual(self.client.post(self.buyer('seller'), {'seller': self.seller.pk}).status_code, 404)

    def test_public_directory_shop_visible_without_enabling_private_access(self):
        self.req.transport_type = 'car'
        self.req.save(update_fields=['transport_type'])
        shop = Seller.objects.create(name='Публичный магазин', whatsapp='77009998877', city='Алматы',
            sells_new_parts=True, all_brands=True, all_categories=True, transport_type='car', catalog_only=True)
        hidden = Seller.objects.create(name='Неопубликованный магазин', whatsapp='77009998877', city='Алматы',
            sells_new_parts=True, all_brands=True, all_categories=True, transport_type='car', is_active=False)
        page = self.client.get(self.buyer('sellers'))
        self.assertContains(page, shop.name)
        shop_url = reverse('workflow_buyer_shop', args=[self.req.pk, self.req.access_token, shop.pk])
        self.assertContains(page, shop_url)
        self.assertContains(self.client.get(shop_url), f'Вы обращаетесь по заявке №{self.req.pk}')
        self.assertNotContains(page, hidden.name)
        shop.refresh_from_db()
        self.assertFalse(shop.direct_contacts_enabled)
        self.assertFalse(shop.contact_access_verified)
        self.assertFalse(Match.objects.filter(request=self.req, seller=shop).exists())
        contact = reverse('connection_buyer_contact', args=[self.req.pk, self.req.access_token, shop.pk, 'whatsapp'])
        self.assertEqual(self.client.post(contact).status_code, 404)

    def test_directory_brand_filter_and_clean_names(self):
        self.req.brand, self.req.transport_type = 'Toyota', 'car'
        self.req.save(update_fields=['brand', 'transport_type'])
        for name in ('СУБАРУ АЛМАТЫ (@subarushopkz) • Instagram profile',
                     'Volkswagen Kazakhstan (@volkswagen_kazakhstan) • Instagram photos and videos',
                     'Тойота Центр Жетысу (@toyota7su_official) • Instagram photos and videos', 'Общий магазин'):
            Seller.objects.create(name=name, whatsapp='77009998877', city='Алматы', sells_new_parts=True,
                all_brands=True, all_categories=True, transport_type='car', catalog_only=True)
        page = self.client.get(self.buyer('sellers'))
        self.assertContains(page, 'Тойота Центр Жетысу')
        self.assertNotContains(page, 'Instagram')
        self.assertNotContains(page, '@toyota7su')
        self.assertNotContains(page, 'СУБАРУ АЛМАТЫ')
        self.assertNotContains(page, 'Volkswagen Kazakhstan')
        names = [i['display_name'] for i in page.context['page'] if i.get('directory_only')]
        self.assertLess(names.index('Тойота Центр Жетысу'), names.index('Общий магазин'))

    def test_directory_contact_tracks_click_without_private_access_or_interest(self):
        self.req.transport_type = 'car'
        self.req.save(update_fields=['transport_type'])
        shop = Seller.objects.create(name='Магазин', whatsapp='77009998877', city='Алматы',
            sells_new_parts=True, all_brands=True, all_categories=True, transport_type='car', catalog_only=True)
        url = reverse('workflow_buyer_directory_contact', args=[self.req.pk, self.req.access_token, shop.pk, 'whatsapp'])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url).status_code, 403)
        self.assertFalse(RequestInteraction.objects.filter(seller=shop, kind='whatsapp_click').exists())
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('https://wa.me/'))
        self.assertNotIn(str(self.req.access_token), response.url)
        self.assertNotIn(self.req.vin, response.url)
        self.assertTrue(RequestInteraction.objects.filter(request=self.req, seller=shop, actor='buyer', kind='whatsapp_click', source='public_directory').exists())
        self.assertFalse(Match.objects.filter(request=self.req, seller=shop).exists())
        self.assertFalse(RequestInteraction.objects.filter(seller=shop, kind__in=['interest', 'contact_confirmed', 'purchase_confirmed']).exists())
        shop.refresh_from_db()
        self.assertFalse(shop.contact_access_verified)
        shop.is_active = False
        shop.save(update_fields=['is_active'])
        self.assertEqual(self.client.post(url).status_code, 404)
        shop.is_active = True
        shop.save(update_fields=['is_active'])
        self.req.access_expires_at = timezone.now() - timedelta(seconds=1)
        self.req.save(update_fields=['access_expires_at'])
        self.assertEqual(self.client.post(url).status_code, 410)
    def test_correction_removes_stale_purchase_and_review(self):
        Match.objects.create(request=self.req, seller=self.seller)
        self.client.post(self.buyer('result'), {'result': 'purchased'})
        self.client.post(self.buyer('seller'), {'seller': self.seller.pk})
        self.client.post(self.buyer('review'), {'rating': 5, 'text': 'Спасибо', 'public_consent': 'on'})
        self.assertTrue(RequestSellerReview.objects.exists())
        self.client.post(self.buyer('search'), {})
        self.req.refresh_from_db()
        self.assertEqual(self.req.purchase_result, 'search_again')
        self.assertFalse(RequestSellerReview.objects.exists())
        self.assertFalse(RequestInteraction.objects.filter(kind='purchase_confirmed').exists())
        self.assertGreaterEqual(RequestOutcomeEvent.objects.count(), 3)
    def test_edit_invalidates_public_summary_and_feed(self):
        self.client.post(self.buyer('edit'), {'brand': 'Mercedes-Benz', 'model': 'E400', 'year': 2015, 'city': 'Алматы', 'description': 'Уточнение'})
        self.req.refresh_from_db()
        self.assertFalse(self.req.seller_feed_enabled)
        self.assertEqual(self.req.public_summary, '')
    def test_expired_buyer_and_wrong_token(self):
        self.req.access_expires_at = timezone.now() - timedelta(seconds=1); self.req.save()
        self.assertEqual(self.client.get(self.buyer('result')).status_code, 410)
        self.assertEqual(self.client.get(reverse('workflow_buyer_step', args=[self.req.pk, uuid4(), 'result'])).status_code, 404)
    @patch('core.workflow_views.limited', return_value=False)
    def test_claim_phone_formats_find_same_card_without_granting_access(self, _limited):
        for phone in ('+7 (700) 111-22-33', '87001112233', '7001112233', '77001112233'):
            with self.subTest(phone=phone):
                response = self.client.post(reverse('workflow_claim'), {'phone': phone})
                self.assertContains(response, 'Это мой магазин')
                self.assertEqual(self.client.session['claim_candidates'], [self.seller.pk])
                self.assertNotIn('_auth_user_id', self.client.session)
        from core.workflow_forms import ClaimPhoneForm
        self.assertFalse(ClaimPhoneForm({'phone': '+7 (700) 11'}).is_valid())

    def test_claim_phone_does_not_authenticate(self):
        self.client.post(reverse('workflow_claim'), {'phone': '77001112233'})
        response = self.client.post(reverse('workflow_claim_confirm', args=[self.seller.pk]), {'comment': 'Это я'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)
        claim = SellerAccessClaim.objects.get()
        claim.status='approved'; claim.login_token=uuid4(); claim.token_expires_at=timezone.now()+timedelta(hours=1); claim.save()
        self.client.get(reverse('workflow_claim_status'))
        self.assertNotIn('_auth_user_id', self.client.session)
    def test_claim_candidate_cannot_be_changed(self):
        self.assertEqual(self.client.post(reverse('workflow_claim_confirm', args=[self.seller.pk])).status_code, 404)
    def test_claim_link_one_use_only(self):
        claim=SellerAccessClaim.objects.create(seller=self.seller, status='approved', login_token=uuid4(), token_expires_at=timezone.now()+timedelta(hours=1))
        url=reverse('workflow_redeem', args=[claim.login_token])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 410)
    def test_signed_in_skips_connect_and_login(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('workflow_connect')).status_code, 302)
        self.assertEqual(self.client.get(reverse('connection_login')).status_code, 302)
    def test_seller_pages_and_call_click_not_sale(self):
        self.client.force_login(self.user)
        participate(self.req, self.seller)
        for name in ('workflow_seller_contact_page', 'workflow_seller_result'):
            self.assertEqual(self.client.get(reverse(name, args=[self.req.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('connection_seller_contact', args=[self.req.pk, 'call'])).status_code, 302)
        self.assertTrue(RequestInteraction.objects.filter(kind='call_click').exists())
        self.req.refresh_from_db(); self.assertEqual(self.req.purchase_result, '')
    def test_contact_page_opens_without_separate_interest_step(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('workflow_seller_contact_page', args=[self.req.pk])).status_code, 200)
    def test_channel_consents_and_binding_not_auto_on_get(self):
        self.client.force_login(self.user)
        for name in ('workflow_channels', 'workflow_telegram_connect', 'workflow_profile_edit', 'connection_quality'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        conn=SellerChannelConnection.objects.get(seller=self.seller)
        self.assertFalse(conn.telegram_consent); self.assertIsNone(conn.binding_token)
        response = self.client.post(reverse('workflow_channels'), {'telegram': 'on'}, follow=True)
        self.assertContains(response, 'Настройки сохранены.')
        self.assertNotContains(self.client.get(reverse('workflow_channels')), 'Настройки сохранены.')
        conn.refresh_from_db(); self.assertTrue(conn.telegram_consent)
        self.client.post(reverse('workflow_channels'), {})
        conn.refresh_from_db(); self.assertFalse(conn.telegram_consent)
    def test_webhook_rejects_untrusted_sender(self):
        self.assertEqual(self.client.post(reverse('workflow_telegram_webhook'), '{}', content_type='application/json').status_code, 403)
    @patch('core.telegram_connection_views._call_api', return_value={'message_id': 1})
    def test_telegram_binding_private_and_one_time(self, api):
        import json
        conn=SellerChannelConnection.objects.create(seller=self.seller, binding_token=uuid4(), binding_expires_at=timezone.now()+timedelta(minutes=10))
        url=reverse('workflow_telegram_webhook')
        def post(data):
            return self.client.post(url, json.dumps(data), content_type='application/json', HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='test-hook-secret')
        post({'message': {'chat': {'type':'group','id':123}, 'from':{'id':123}, 'text':'/start bind_'+conn.binding_token.hex}})
        conn.refresh_from_db(); self.assertIsNone(conn.pending_telegram_id)
        post({'message': {'chat': {'type':'private','id':123}, 'from':{'id':123}, 'text':'/start bind_'+conn.binding_token.hex}})
        post({'callback_query': {'from': {'id': 124}, 'message':{'chat':{'type':'private','id':124}}, 'data':'bind:'+conn.binding_token.hex}})
        conn.refresh_from_db(); self.assertIsNone(conn.telegram_id)
        post({'callback_query': {'from': {'id': 123}, 'message':{'chat':{'type':'private','id':123}}, 'data':'bind:'+conn.binding_token.hex}})
        conn.refresh_from_db(); self.assertEqual(conn.telegram_id,123); self.assertIsNone(conn.binding_token); self.assertFalse(conn.telegram_consent)
    @patch('core.services.workflow_notifications._call_api', side_effect=TelegramChannelError('timeout'))
    def test_unknown_notice_cannot_retry(self, api):
        SellerChannelConnection.objects.create(seller=self.seller, telegram_id=123, telegram_consent=True)
        with self.assertRaises(ValueError): send_one(self.req,self.seller,self.staff)
        with self.assertRaises(ValueError): send_one(self.req,self.seller,self.staff)
        self.assertEqual(api.call_count,1)
        self.assertEqual(RequestSellerNotification.objects.get().status,'unknown')
    def test_admin_pages_and_unauthorized_access(self):
        names=[('workflow_admin_list',[]),('workflow_admin_control',[self.req.pk]),('workflow_admin_reconcile',[self.req.pk]),('workflow_admin_manual',[self.req.pk]),('workflow_admin_activity',[self.seller.pk])]
        for name,args in names:
            self.assertNotEqual(self.client.get(reverse(name,args=args)).status_code,200)
        self.client.force_login(self.staff)
        for name,args in names:
            self.assertEqual(self.client.get(reverse(name,args=args)).status_code,200,name)
    def test_reconcile_rejects_other_channel_and_preserves_id(self):
        self.client.force_login(self.staff)
        url=reverse('workflow_admin_reconcile',args=[self.req.pk])
        self.client.post(url,{'url':'https://t.me/other/1','reviewed':'on'})
        from core.models import TelegramRequestPublication
        self.assertFalse(TelegramRequestPublication.objects.filter(request=self.req).exists())
        self.client.post(url,{'url':'https://t.me/zpt_kz_zakaz/123','reviewed':'on'})
        self.assertEqual(self.req.telegram_publication.message_id,123)
        self.client.post(url,{'url':'https://t.me/zpt_kz_zakaz/124','reviewed':'on'})
        self.req.telegram_publication.refresh_from_db(); self.assertEqual(self.req.telegram_publication.message_id,123)
    def test_csrf_protection(self):
        client=Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.buyer('result'),{'result':'purchased'}).status_code,403)
    @patch('core.telegram_connection_views._call_api', return_value={'message_id':1})
    def test_telegram_login_needs_bot_confirmation_and_original_browser(self, api):
        import json
        from core.models import TelegramSellerLogin
        SellerChannelConnection.objects.create(seller=self.seller, telegram_id=555)
        url=reverse('workflow_telegram_login')
        self.client.post(url,{'action':'start'})
        ticket=TelegramSellerLogin.objects.get()
        other=Client()
        other.post(url,{'action':'check'})
        self.assertNotIn('_auth_user_id',other.session)
        self.client.post(url,{'action':'check'})
        self.assertNotIn('_auth_user_id',self.client.session)
        self.client.post(reverse('workflow_telegram_webhook'),json.dumps({'callback_query':{'from':{'id':555}, 'message':{'chat':{'type':'private','id':555}}, 'data':'login:'+ticket.pk.hex}}),content_type='application/json',HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='test-hook-secret')
        self.client.post(url,{'action':'check'})
        self.assertEqual(int(self.client.session['_auth_user_id']),self.user.pk)
        ticket.refresh_from_db(); self.assertIsNotNone(ticket.consumed_at)
    @patch('core.services.workflow_notifications._call_api')
    def test_subscription_batch_never_backfills_old_request(self, api):
        from core.models import SellerChannelConsentEvent
        from django.core.management import call_command
        from io import StringIO
        SellerChannelConnection.objects.create(seller=self.seller, telegram_id=555, telegram_consent=True)
        SellerChannelConsentEvent.objects.create(seller=self.seller,channel='telegram',granted=True)
        call_command('notify_telegram_request_subscribers',stdout=StringIO())
        api.assert_not_called()
        self.assertFalse(RequestSellerNotification.objects.exists())
    @patch('core.services.workflow_notifications._call_api', return_value={'message_id': 777})
    def test_explicit_telegram_delivery_records_send_without_contact(self, api):
        from django.core.management import call_command
        from io import StringIO
        SellerChannelConnection.objects.create(seller=self.seller, telegram_id=555, telegram_consent=True)
        args = {'request': self.req.pk, 'seller': self.seller.pk, 'actor': self.staff.pk}
        call_command('send_seller_telegram_request', stdout=StringIO(), **args)
        api.assert_not_called()
        call_command('send_seller_telegram_request', apply=True, stdout=StringIO(), **args)
        self.assertEqual(api.call_count, 1)
        notice = RequestSellerNotification.objects.get(request=self.req, seller=self.seller)
        self.assertEqual((notice.status, notice.message_id), ('sent', 777))
        payload = api.call_args.args[1]
        self.assertNotIn(self.req.phone, payload['text'])
        self.assertNotIn(self.req.vin, payload['text'])
        target = payload['reply_markup']['inline_keyboard'][0][0]['url']
        self.assertNotIn(str(self.req.access_token), target)
        self.assertFalse(RequestInteraction.objects.filter(request=self.req).exists())
        self.req.refresh_from_db()
        self.assertEqual(self.req.purchase_result, '')
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('send_seller_telegram_request', apply=True, stdout=StringIO(), **args)
        self.assertEqual(api.call_count, 1)

    @patch('core.management.commands.dispatch_request_waves.process_due_service_request_dispatches')
    @patch('core.management.commands.dispatch_request_waves.process_due_dispatch_waves')
    @patch('core.services.workflow_notifications._call_api', return_value={'message_id': 778})
    def test_worker_delivers_only_new_reviewed_opt_in_requests(self, api, parts, services):
        from django.core.management import call_command
        from io import StringIO
        from core.models import SellerChannelConsentEvent
        SellerChannelConnection.objects.create(seller=self.seller, telegram_id=555, telegram_consent=True)
        consent = SellerChannelConsentEvent.objects.create(seller=self.seller, channel='telegram', granted=True)
        cutoff = timezone.now()
        req = Request.objects.create(brand='Toyota', model='Camry', city='Алматы', phone='',
            description='Synthetic worker test', public_summary='Тест уведомления', seller_feed_enabled=True)
        Request.objects.create(phone='', public_summary='На проверке', seller_feed_enabled=False)
        revoked = Request.objects.create(phone='', public_summary='Отозвана', seller_feed_enabled=True, access_revoked=True)
        # Disabled by default even when subscriptions exist.
        call_command('dispatch_request_waves', stdout=StringIO(), stderr=StringIO())
        api.assert_not_called()
        with self.settings(TELEGRAM_SUBSCRIBER_DELIVERY_ENABLED=True,
                TELEGRAM_SUBSCRIBER_DELIVERY_ACTOR_ID=str(self.staff.pk),
                TELEGRAM_SUBSCRIBER_DELIVERY_SINCE=cutoff.isoformat()):
            call_command('dispatch_request_waves', stdout=StringIO(), stderr=StringIO())
            self.assertEqual(api.call_count, 1)
            notice = RequestSellerNotification.objects.get()
            self.assertEqual(notice.request_id, req.pk)
            call_command('dispatch_request_waves', stdout=StringIO(), stderr=StringIO())
            self.assertEqual(api.call_count, 1)
            # Consent withdrawal blocks later eligible requests.
            conn = SellerChannelConnection.objects.get(seller=self.seller)
            conn.telegram_consent = False
            conn.save()
            Request.objects.create(phone='', public_summary='Следующая', seller_feed_enabled=True)
            call_command('dispatch_request_waves', stdout=StringIO(), stderr=StringIO())
            self.assertEqual(api.call_count, 1)
        self.assertFalse(RequestInteraction.objects.exists())
        self.assertFalse(RequestSellerNotification.objects.filter(request=revoked).exists())

    def test_delivery_rejects_invalid_start_timestamp(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        for value in ('bad', '2026-10-24T12:00:00'):
            with self.assertRaises(CommandError):
                call_command('notify_telegram_request_subscribers', since=value, stdout=StringIO())

    def test_imported_new_shop_duplicate_phone_uses_existing_card(self):
        count=Seller.objects.count()
        self.client.post(reverse('workflow_claim')+'?new=1',{'name':'Другой','phone':'77001112233','city':'Алматы','consent':'on'})
        self.assertEqual(Seller.objects.count(),count)
        self.assertNotIn('_auth_user_id',self.client.session)
    @patch('core.telegram_connection_views._call_api',return_value={'message_id':1})
    def test_buyer_bot_binding_needs_private_token_and_consent(self,api):
        import json
        from core.models import RequestBuyerChannel
        url=reverse('workflow_buyer_channels',args=[self.req.pk,self.req.access_token])
        self.client.post(url,{'consent':'on'})
        conn=RequestBuyerChannel.objects.get(request=self.req)
        token=conn.binding_token.hex
        def post(data):
            return self.client.post(reverse('workflow_telegram_webhook'),json.dumps(data),content_type='application/json',HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='test-hook-secret')
        post({'message':{'chat':{'type':'private','id':777},'from':{'id':777},'text':'/start buyer_'+token}})
        conn.refresh_from_db(); self.assertIsNone(conn.telegram_id)
        post({'callback_query':{'from':{'id':777},'message':{'chat':{'type':'private','id':777}},'data':'buyer:'+token}})
        conn.refresh_from_db(); self.assertEqual(conn.telegram_id,777)
        self.client.post(url,{'action':'disconnect'})
        conn.refresh_from_db(); self.assertIsNone(conn.telegram_id); self.assertFalse(conn.telegram_consent)
    def test_buyer_followup_manual_requires_confirmation_and_staff(self):
        from core.models import RequestBuyerFollowup
        url=reverse('workflow_admin_buyer_followup',args=[self.req.pk])
        self.assertNotEqual(self.client.get(url).status_code,200)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertFalse(RequestBuyerFollowup.objects.exists())
        self.client.post(url,{'action':'manual'})
        self.assertFalse(RequestBuyerFollowup.objects.exists())
        self.client.post(url,{'action':'manual','confirmed':'on'})
        self.assertEqual(RequestBuyerFollowup.objects.get().status,'manual')

    @patch('core.management.commands.send_buyer_result_question._call_api', return_value={'message_id': 901})
    def test_result_question_channels_are_explicit_and_do_not_confirm_purchase(self, api):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        from core.models import RequestBuyerChannel, RequestBuyerFollowup
        args = {'request': self.req.pk, 'actor': self.staff.pk, 'stdout': StringIO()}
        with self.assertRaises(CommandError):
            call_command('send_buyer_result_question', apply=True, **args)
        api.assert_not_called()
        RequestBuyerChannel.objects.create(request=self.req, telegram_id=123, telegram_consent=True)
        call_command('send_buyer_result_question', **args)
        api.assert_not_called()
        call_command('send_buyer_result_question', apply=True, **args)
        self.assertEqual(api.call_count, 1)
        with self.assertRaises(CommandError):
            call_command('send_buyer_result_question', apply=True, **args)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(RequestBuyerFollowup.objects.get().status, 'sent')
        self.req.refresh_from_db()
        self.assertNotIn(self.req.purchase_result, ('purchased', 'closed'))
        self.assertFalse(RequestInteraction.objects.filter(kind__in=['contact_confirmed', 'purchase_confirmed']).exists())

    def test_close_and_repeat_search_require_buyer_confirmation_and_preserve_history(self):
        for choice, expected in [('close', 'closed'), ('search', 'search_again')]:
            response = self.client.post(self.buyer('result'), {'result': choice})
            self.assertEqual(response.status_code, 302)
            self.req.refresh_from_db()
            self.assertNotEqual(self.req.purchase_result, expected)
            for _ in range(2):
                self.client.post(self.buyer(choice), {'reason': 'Не нашёл', 'comment': 'Тест результата'})
            self.req.refresh_from_db()
            self.assertEqual(self.req.purchase_result, expected)
            self.assertEqual(RequestOutcomeEvent.objects.filter(request=self.req, result=expected).count(), 1)
        self.assertFalse(RequestInteraction.objects.filter(kind='purchase_confirmed').exists())
        self.assertFalse(RequestSellerNotification.objects.exists())


@override_settings(TWO_WAY_REQUESTS_ENABLED=True, ALLOWED_HOSTS=['testserver'])
class NewShopSpecializationTests(TestCase):
    def setUp(self):
        from core.models import Country, Brand, PartCategory
        country = Country.objects.create(name='Япония')
        self.brand = Brand.objects.create(name='Toyota', country=country)
        self.other = Brand.objects.create(name='Lexus', country=country)
        self.truck = Brand.objects.create(name='Hino', country=country, transport_type='truck')
        self.category = PartCategory.objects.create(name='Фильтры')
        self.other_category = PartCategory.objects.create(name='Тормоза')
        self.url = reverse('workflow_claim') + '?new=1'
        self.data = dict(name='Новый магазин', phone='77009991122', city='Астана', consent='on',
                         brand=[self.brand.pk, self.other.pk], category=[self.category.pk, self.other_category.pk])

    def test_choices_render_and_multiple_selections_are_saved_without_granting_access(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'Toyota')
        self.assertContains(page, 'Фильтры')
        self.assertNotContains(page, 'Hino')
        self.assertContains(page, 'type="checkbox" name="brand"')
        result = self.client.post(self.url, self.data)
        self.assertRedirects(result, reverse('workflow_claim_status'))
        seller = Seller.objects.get(whatsapp='77009991122')
        self.assertSetEqual(set(seller.selected_brands.values_list('pk', flat=True)), {self.brand.pk, self.other.pk})
        self.assertSetEqual(set(seller.selected_categories.values_list('pk', flat=True)), {self.category.pk, self.other_category.pk})
        self.assertEqual(seller.city, 'Астана')
        self.assertTrue(seller.catalog_only)
        self.assertFalse(seller.contact_access_verified)
        self.assertIsNone(seller.sells_new_parts)
        self.assertFalse(seller.is_dismantler)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_invalid_choices_are_rejected_and_valid_choices_survive_other_errors(self):
        result = self.client.post(self.url, {**self.data, 'brand':[self.truck.pk]})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.context['form'].errors.get('brand'))
        self.assertFalse(Seller.objects.exists())
        result = self.client.post(self.url, {**self.data, 'name':''})
        self.assertEqual(result.status_code, 200)
        self.assertRegex(result.content.decode(), rf'name="brand"[^>]*value="{self.brand.pk}"[^>]*checked')
        self.assertRegex(result.content.decode(), rf'name="category"[^>]*value="{self.category.pk}"[^>]*checked')
        self.assertFalse(Seller.objects.exists())

    def test_optional_specialization_can_be_completed_later(self):
        result = self.client.post(self.url, {**self.data, 'brand':[], 'category':[]})
        self.assertEqual(result.status_code, 302)
        seller = Seller.objects.get(whatsapp='77009991122')
        self.assertFalse(seller.selected_brands.exists())
        self.assertFalse(seller.selected_categories.exists())


@override_settings(TWO_WAY_REQUESTS_ENABLED=True, ALLOWED_HOSTS=['testserver'])
class ShopClaimDuplicateTests(TestCase):
    def test_test_mirror_is_hidden_without_deleting_or_merging_accounts(self):
        real = Seller.objects.create(name='AG Parts', city='Алматы', whatsapp='77713607040')
        mirror = Seller.objects.create(name='AG Parts', city='Алматы', whatsapp='77713607040', is_test_seller=True)
        branch = Seller.objects.create(name='Другой магазин', city='Алматы', whatsapp='77713607040')
        response = self.client.post(reverse('workflow_claim'), {'phone':'+7 (771) 360-70-40'})
        self.assertEqual([s['pk'] for s in response.context['candidates']], [real.pk, branch.pk])
        self.assertEqual(self.client.session['claim_candidates'], [real.pk, branch.pk])
        self.assertTrue(Seller.objects.filter(pk=mirror.pk, is_test_seller=True).exists())
        self.assertNotContains(response, '<svg')

    def test_overlong_phone_is_rejected_without_a_lookup(self):
        with patch('core.workflow_views.find_sellers_by_phone') as lookup:
            response = self.client.post(reverse('workflow_claim'), {'phone':'+7 (771) 360-70-401'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('phone'))
        lookup.assert_not_called()


@override_settings(REQUEST_OWNER_EMAIL_ENABLED=True, REQUEST_OWNER_EMAIL='owner@test.local',
                  EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', DEFAULT_FROM_EMAIL='zpt@test.local')
class SellerAccessOwnerNoticeTests(TestCase):
    def setUp(self):
        self.seller = Seller.objects.create(name='AG Parts', whatsapp='77713607040', city='Алматы')
        self.claim = SellerAccessClaim.objects.create(seller=self.seller)

    def test_owner_receives_one_notice_without_login_token_or_auto_approval(self):
        from django.core import mail
        from core.services.seller_access_notifications import process_owner_notices
        self.claim.login_token = uuid4()
        self.claim.save()
        self.assertEqual(process_owner_notices(), 1)
        self.assertEqual(process_owner_notices(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['owner@test.local'])
        self.assertIn(reverse('admin:core_selleraccessclaim_change', args=[self.claim.pk]), mail.outbox[0].body)
        self.assertNotIn(str(self.claim.login_token), mail.outbox[0].body)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, 'pending')
        self.assertEqual(self.claim.owner_notice_status, 'sent')
        self.assertIsNotNone(self.claim.owner_notice_sent_at)
        self.assertEqual(self.claim.owner_notice_recipient, 'owner@test.local')
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.contact_access_verified)

    def test_failed_or_inflight_notice_is_not_retried_automatically(self):
        from core.services.seller_access_notifications import process_owner_notices
        with patch('core.services.seller_access_notifications.send_mail', side_effect=TimeoutError):
            self.assertEqual(process_owner_notices(), 0)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.owner_notice_status, 'failed')
        with patch('core.services.seller_access_notifications.send_mail') as sender:
            process_owner_notices()
            self.claim.owner_notice_status = 'sending'
            self.claim.save()
            process_owner_notices()
        sender.assert_not_called()

    def test_notice_is_claimed_before_smtp_and_nested_worker_cannot_duplicate(self):
        from core.services.seller_access_notifications import process_owner_notices
        def send(**kwargs):
            self.assertEqual(process_owner_notices(), 0)
            return 1
        with patch('core.services.seller_access_notifications.send_mail', side_effect=send) as sender:
            self.assertEqual(process_owner_notices(), 1)
        sender.assert_called_once()

    def test_disabled_mail_and_reviewed_claims_are_not_sent(self):
        from core.services.seller_access_notifications import process_owner_notices
        with self.settings(REQUEST_OWNER_EMAIL_ENABLED=False):
            self.assertEqual(process_owner_notices(), 0)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.owner_notice_status, 'pending')
        self.claim.status = 'approved'
        self.claim.save()
        with patch('core.services.seller_access_notifications.send_mail') as sender:
            self.assertEqual(process_owner_notices(), 0)
        sender.assert_not_called()

    def test_backfill_only_queues_reported_claim_and_preserves_sent_results(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        batch = import_module('core.migrations.0131_seller_access_owner_notices')
        target = Seller.objects.create(pk=569, name='Target', whatsapp='77001112244')
        reported = SellerAccessClaim.objects.create(pk='b2332467-1e87-432c-a79e-8c725628cc01',
            seller=target, owner_notice_status='skipped')
        self.claim.owner_notice_status = 'skipped'
        self.claim.save()
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        batch.enqueue_reported_claim(apps, editor)
        reported.refresh_from_db(); self.claim.refresh_from_db()
        self.assertEqual(reported.owner_notice_status, 'pending')
        self.assertEqual(self.claim.owner_notice_status, 'skipped')
        reported.owner_notice_status = 'sent'; reported.save()
        batch.undo_enqueue(apps, editor)
        reported.refresh_from_db()
        self.assertEqual(reported.owner_notice_status, 'sent')
