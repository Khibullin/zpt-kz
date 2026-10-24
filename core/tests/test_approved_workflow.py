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
    def test_no_contact_without_interest(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('workflow_seller_contact_page', args=[self.req.pk])).status_code, 403)
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
