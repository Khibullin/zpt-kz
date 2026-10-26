from django.contrib.auth.models import Permission, User
from django.contrib.admin.models import LogEntry
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone
from core.models import Request, Seller, RequestDispatch, Match


@override_settings(ALLOWED_HOSTS=['*'], PUBLIC_BASE_URL='https://zpt.kz')
class ManualWorkspaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('operator', is_staff=True)
        self.user.user_permissions.add(Permission.objects.get(codename='change_request', content_type__app_label='core'))
        self.req = Request.objects.create(source='home_short', transport_type='car', brand='BMW',
            model='5 Series', year=1993, city='Актобе', category='Топливная система',
            search_scope='city', description='Форсунки', phone='77072812731',contact_consent_at=timezone.now())
        self.seller = Seller.objects.create(name='BMW Актобе', whatsapp='77011013032',
            city='Актобе', transport_type='car', sells_new_parts=True, all_brands=True,
            all_categories=True, is_active=True, is_paused=False, catalog_only=False)
        self.url = reverse('control_panel:parts_request_process', args=[self.req.pk])
        self.client.force_login(self.user)

    def test_access_and_read_only_get(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.user)
        self.user.user_permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.user.user_permissions.add(Permission.objects.get(codename='change_request', content_type__app_label='core'))
        response = self.client.get(self.url)
        self.assertContains(response, 'Задать вопрос покупателю')
        self.assertContains(response, 'Выбрать продавцов')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertFalse(RequestDispatch.objects.exists())

    def test_details_notes_and_manual_delivery(self):
        response = self.client.post(self.url, {'action':'save_details', 'description':'Форсунки M50, 6 шт.', 'article':'ABC', 'vin':'VIN123'})
        self.assertRedirects(response, self.url)
        self.req.refresh_from_db()
        self.assertEqual(self.req.description, 'Форсунки M50, 6 шт.')
        self.assertEqual(self.req.search_scope, 'city')
        self.client.post(self.url, {'action':'note', 'note':'Ждём фото форсунки'})
        self.assertTrue(LogEntry.objects.filter(change_message__contains='Ждём фото').exists())
        self.client.post(self.url, {'action':'prepare', 'seller_id':self.seller.pk})
        self.assertFalse(RequestDispatch.objects.exists())
        self.client.post(self.url, {'action':'prepare', 'seller_id':self.seller.pk,
            'one_time_permission':'yes', 'permission_evidence':'26.10.2026: по телефону продавец согласился'})
        dispatch = RequestDispatch.objects.get()
        self.assertEqual(dispatch.status, 'prepared')
        self.assertFalse(Match.objects.exists())
        self.assertContains(self.client.get(self.review()), 'Отправить в WhatsApp')
        self.client.post(self.url, {'action':'confirm_sent', 'dispatch_id':dispatch.pk})
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, 'prepared')
        self.client.post(self.url, {'action':'confirm_sent', 'dispatch_id':dispatch.pk, 'did_send':'yes'})
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, 'sent')
        self.assertEqual(Match.objects.get().status, 'sent')

    def test_outside_city_and_csrf_are_rejected(self):
        self.seller.city = 'Алматы'
        self.seller.save(update_fields=['city'])
        self.client.post(self.url, {'action':'prepare', 'seller_id':self.seller.pk,
            'one_time_permission':'yes', 'permission_evidence':'26.10.2026: продавец дал разрешение'})
        self.assertFalse(RequestDispatch.objects.exists())
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url, {'action':'note', 'note':'x'}).status_code, 403)

    def review(self):
        return reverse('control_panel:parts_request_dispatch_review', args=[self.req.pk])

    def picker(self):
        return reverse('control_panel:parts_request_sellers', args=[self.req.pk])

    def test_picker_includes_channel_consent_and_catalog_reason(self):
        from django.utils import timezone
        from datetime import timedelta
        from core.models import SellerChannelConnection
        owner = Seller.objects.create(name='AG Parts', whatsapp='77011013033', city='Актобе',
            transport_type='car', is_active=True, sells_new_parts=True, all_brands=True, all_categories=True,
            test_all_requests_since=self.req.created_at - timedelta(days=1))
        SellerChannelConnection.objects.create(seller=owner, whatsapp_consent=True)
        catalog = Seller.objects.create(name='Каталог Актобе', city='Актобе', transport_type='car',
            whatsapp='77011013034', catalog_only=True, all_brands=True, all_categories=True, sells_new_parts=True)
        response = self.client.get(self.picker())
        self.assertContains(response, 'AG Parts')
        self.assertContains(response, 'Есть согласие')
        self.assertContains(response, 'Можно отправить вручную')
        response = self.client.post(self.picker(), {'action':'prepare', 'seller_ids':[catalog.pk], 'one_time_permission':'yes'})
        catalog_dispatch = RequestDispatch.objects.get(seller=catalog)
        self.assertIn('Устное разрешение', catalog_dispatch.permission_evidence)
        catalog.refresh_from_db()
        self.assertTrue(catalog.catalog_only)
        response = self.client.post(reverse('control_panel:parts_request_manual_send', args=[self.req.pk, catalog_dispatch.pk]))
        self.assertTrue(response.url.startswith('https://wa.me/77011013034?text='))
        response = self.client.post(self.picker(), {'action':'prepare', 'seller_ids':[owner.pk]})
        self.assertEqual(response.status_code, 302)
        dispatch = RequestDispatch.objects.get(seller=owner)
        self.assertIn('whatsapp_channel', dispatch.permission_evidence)
        self.assertFalse(Match.objects.exists())
        send_url = reverse('control_panel:parts_request_manual_send', args=[self.req.pk, dispatch.pk])
        response = self.client.post(send_url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('https://wa.me/77011013033?text='))
        self.assertEqual(self.client.get(send_url).status_code, 405)
        self.assertContains(self.client.get(self.picker()+'?consent=granted'), 'AG Parts')
        self.assertNotIn(catalog.pk, [row['seller'].pk for row in self.client.get(self.picker()+'?consent=granted').context['page']])

    def test_test_sellers_hidden_and_cannot_enter_manual_basket(self):
        from datetime import timedelta
        from core.services.test_receivers import test_receiver_decision
        self.seller.is_test_seller = True
        self.seller.test_all_requests_since = self.req.created_at - timedelta(days=1)
        self.seller.save()
        self.assertIs(test_receiver_decision(self.seller, self.req), True)
        for query in ('', '?scope=all', '?tab=selected', '?tab=blocked', '?consent=granted'):
            self.assertNotContains(self.client.get(self.picker()+query), self.seller.name)
        self.client.post(self.picker(), {'action':'select_all'})
        key = f'manual_recipients:{self.req.pk}'
        self.assertNotIn(self.seller.pk, self.client.session[key])
        response = self.client.post(self.picker(), {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'yes'})
        self.assertEqual(response.status_code, 400)
        session = self.client.session
        session[key] = [self.seller.pk]
        session.save()
        self.assertEqual(self.client.get(self.review()).context['selected_count'], 0)
        self.client.post(self.review(), {'action':'prepare', 'one_time_permission':'yes'})
        self.assertFalse(RequestDispatch.objects.exists())
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.is_test_seller)
        self.assertIs(test_receiver_decision(self.seller, self.req), True)

    def test_sent_one_time_consent_display_and_filter_are_request_specific(self):
        from core.models import SellerContactConsent
        key = f'manual_recipients:{self.req.pk}'
        self.client.post(self.picker(), {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'yes'})
        self.client.post(self.review(), {'action':'prepare', 'one_time_permission':'yes'})
        dispatch = RequestDispatch.objects.get(seller=self.seller)
        self.assertNotContains(self.client.get(self.picker()), 'Устное согласие на эту заявку')
        self.client.post(self.review(), {'action':'confirm_sent', 'dispatch_id':dispatch.pk, 'did_send':'yes'})
        self.assertContains(self.client.get(self.picker()+'?tab=sent'), 'Устное согласие на эту заявку')
        self.assertContains(self.client.get(self.picker()+'?tab=sent&consent=granted'), self.seller.name)
        self.assertNotContains(self.client.get(self.picker()+'?tab=sent&consent=missing'), self.seller.name)
        self.assertFalse(SellerContactConsent.objects.exists())
        other = Request.objects.create(source='home_short', transport_type='car', brand='BMW', city='Актобе',
            category='Топливная система', search_scope='city', phone='77072812731')
        other_url = reverse('control_panel:parts_request_sellers', args=[other.pk])
        self.assertContains(self.client.get(other_url), 'Нет записи')
        dispatch.permission_evidence = ''
        dispatch.save(update_fields=['permission_evidence'])
        self.assertNotContains(self.client.get(self.picker()+'?tab=sent'), 'Устное согласие на эту заявку')

    def test_tab_counts_remain_complete_on_selected_tab_and_follow_filters(self):
        self.client.post(self.picker(), {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'yes'})
        second = Seller.objects.create(name='Второй магазин', city='Актобе', transport_type='car',
            whatsapp='77011013039', sells_new_parts=True, all_brands=True, all_categories=True)
        for tab in ('ready', 'selected', 'sent', 'blocked'):
            response = self.client.get(self.picker()+'?tab='+tab)
            self.assertEqual(response.context['counts']['ready'], 2)
            self.assertEqual(response.context['counts']['sent'], 0)
            self.assertContains(response, 'Можно отправить — 2')
            self.assertContains(response, 'Уже отправлено — 0')
        response = self.client.get(self.picker()+'?tab=selected&q=Второй')
        self.assertEqual(response.context['counts']['ready'], 1)
        self.assertEqual(response.context['selected_count'], 1)
        self.assertContains(response, self.seller.name)

    def test_selection_spans_pages_and_filters_and_revalidates_post(self):
        from core.models import SellerChannelConnection
        for n in range(55):
            seller = Seller.objects.create(name=f'Магазин {n:02}', city='Актобе', transport_type='car',
                whatsapp=f'7701001{n:04}', sells_new_parts=True, all_brands=True, all_categories=True)
            SellerChannelConnection.objects.create(seller=seller, whatsapp_consent=True)
        response = self.client.get(self.picker())
        self.assertEqual(response.context['total'], 56)
        self.client.post(self.picker(), {'action':'select_all'})
        key = f'manual_recipients:{self.req.pk}'
        self.assertEqual(len(self.client.session[key]),56)
        response = self.client.get(self.picker()+'?page=2')
        self.assertTrue(all(row['selected'] for row in response.context['page']))
        self.seller.city = 'Алматы'; self.seller.save(update_fields=['city'])
        self.client.post(self.picker()+'?page=2', {'action':'prepare', 'seller_ids':[],
            'one_time_permission':'yes', 'permission_evidence':'26.10.2026: телефонное разрешение продавцов'})
        self.assertFalse(RequestDispatch.objects.filter(seller=self.seller).exists())
        self.assertTrue(RequestDispatch.objects.exists())

    @override_settings(TWO_WAY_REQUESTS_ENABLED=True)
    def test_manual_token_opens_bound_request_without_claiming_account(self):
        from core.services.manual_seller_dispatch import prepare_manual_dispatch
        from django.utils import timezone
        self.req.contact_consent_at = timezone.now()
        self.req.save(update_fields=['contact_consent_at'])
        dispatch, access = prepare_manual_dispatch(self.req,self.seller,
            permission_evidence='26.10.2026: разрешение по телефону')
        self.client.logout()
        url = reverse('seller_request_link',args=[access.token])
        response = self.client.get(url)
        self.assertContains(response,'Форсунки')
        self.assertNotContains(response,'Найти свою карточку')
        self.assertContains(response,'wa.me/')
        self.assertNotIn('_auth_user_id',self.client.session)
        self.seller.refresh_from_db()
        self.assertIsNone(self.seller.user_id)
        self.req.access_revoked = True; self.req.save(update_fields=['access_revoked'])
        self.assertEqual(self.client.get(url).status_code,403)

    def test_sent_recipient_cannot_be_selected_again_and_selection_is_cleared(self):
        from core.models import SellerChannelConnection
        SellerChannelConnection.objects.create(seller=self.seller, whatsapp_consent=True)
        self.client.post(self.picker(), {'action':'prepare','seller_ids':[self.seller.pk]})
        dispatch=RequestDispatch.objects.get()
        self.client.post(self.picker(), {'action':'confirm_sent','dispatch_id':dispatch.pk,'did_send':'yes'})
        self.assertNotIn(self.seller.pk,self.client.session[f'manual_recipients:{self.req.pk}'])
        response=self.client.get(self.picker()+'?tab=sent')
        self.assertContains(response,'Эта заявка уже отправлена магазину.')
        self.assertEqual(response.context['ready'],0)
        self.client.post(self.picker(), {'action':'select_all'})
        self.assertEqual(self.client.session[f'manual_recipients:{self.req.pk}'],[])
        url=reverse('control_panel:parts_request_manual_send',args=[self.req.pk,dispatch.pk])
        self.assertEqual(self.client.post(url).status_code,404)
        self.assertEqual(RequestDispatch.objects.count(),1)

    def test_send_rechecks_consent_and_request_lifecycle(self):
        from core.models import SellerChannelConnection
        conn=SellerChannelConnection.objects.create(seller=self.seller,whatsapp_consent=True)
        self.client.post(self.picker(), {'action':'prepare','seller_ids':[self.seller.pk]})
        dispatch=RequestDispatch.objects.get()
        url=reverse('control_panel:parts_request_manual_send',args=[self.req.pk,dispatch.pk])
        conn.whatsapp_consent=False;conn.save(update_fields=['whatsapp_consent'])
        self.assertRedirects(self.client.post(url),self.picker())
        conn.whatsapp_consent=True;conn.save(update_fields=['whatsapp_consent'])
        self.req.purchase_result='closed';self.req.save(update_fields=['purchase_result'])
        self.assertRedirects(self.client.post(url),self.picker())
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status,'prepared')
        self.assertFalse(Match.objects.exists())

    def test_manual_countrywide_selection_is_explicit_and_keeps_buyer_scope(self):
        from core.models import SellerChannelConnection
        from core.services.manual_seller_dispatch import validate_manual_recipient
        from django.core.exceptions import ValidationError
        other=Seller.objects.create(name='Внешний магазин',city='Алматы',transport_type='car',
            whatsapp='77011013999',sells_new_parts=True,all_brands=True,all_categories=True)
        SellerChannelConnection.objects.create(seller=other,whatsapp_consent=True)
        self.assertNotContains(self.client.get(self.picker()),'Внешний магазин')
        expanded=self.picker()+'?scope=all&city=Алматы'
        response=self.client.get(expanded)
        self.assertContains(response,'Внешний магазин')
        self.assertContains(response,'Другой город')
        self.client.post(expanded,{'action':'prepare','seller_ids':[other.pk]})
        dispatch=RequestDispatch.objects.get(seller=other)
        self.assertTrue(LogEntry.objects.filter(change_message=f'manual_territory_override:{other.pk}:Алматы').exists())
        self.req.refresh_from_db()
        self.assertEqual(self.req.search_scope,'city')
        self.assertEqual(self.req.city,'Актобе')
        with self.assertRaises(ValidationError):
            validate_manual_recipient(self.req,other)
        send=reverse('control_panel:parts_request_manual_send',args=[self.req.pk,dispatch.pk])
        self.assertTrue(self.client.post(send).url.startswith('https://wa.me/77011013999?'))
        # An audit decision for one city cannot silently authorize a later move.
        other.city='Астана';other.save(update_fields=['city'])
        self.assertRedirects(self.client.post(send),self.picker())

    def test_expansion_does_not_bypass_specialization_or_selected_cities_default(self):
        from core.models import SellerChannelConnection
        other=Seller.objects.create(name='За пределами выбранных',city='Алматы',transport_type='car',
            whatsapp='77011013998',sells_new_parts=True,all_brands=True,all_categories=True)
        SellerChannelConnection.objects.create(seller=other,whatsapp_consent=True)
        self.req.search_scope='custom';self.req.selected_cities='Актобе, Атырау'
        self.req.save(update_fields=['search_scope','selected_cities'])
        self.assertNotContains(self.client.get(self.picker()),other.name)
        self.client.post(self.picker(),{'action':'prepare','seller_ids':[other.pk]})
        self.assertFalse(RequestDispatch.objects.filter(seller=other).exists())
        self.assertContains(self.client.get(self.picker()+'?scope=all'),other.name)
        other.all_categories=False;other.category='Кузов'
        other.save(update_fields=['all_categories','category'])
        self.assertNotContains(self.client.get(self.picker()+'?scope=all'),other.name)


    def test_clarification_hint_and_editable_question_do_not_block_selection(self):
        from urllib.parse import urlsplit, parse_qs
        response = self.client.get(self.url)
        self.assertContains(response, 'Недостаточно данных для точного подбора.')
        self.assertContains(response, 'количество форсунок')
        self.assertContains(response, 'Выбрать продавцов')
        question = 'Уточните двигатель и пришлите фото: номер на форсунке?'
        response = self.client.post(self.url, {'action': 'buyer_question', 'question': question})
        self.assertEqual(response.status_code, 302)
        target = urlsplit(response.url)
        self.assertEqual(target.netloc, 'wa.me')
        self.assertEqual(parse_qs(target.query)['text'], [question])
        self.assertFalse(RequestDispatch.objects.exists())
        self.assertFalse(Match.objects.exists())
        self.req.vin = 'VIN123'; self.req.save(update_fields=['vin'])
        self.assertNotContains(self.client.get(self.url), 'Недостаточно данных для точного подбора.')

    def test_historical_request_consent_is_recognized_without_creating_new_consent(self):
        from core.models import SellerContactConsent, CONTACT_CONSENT_SOURCE_REGISTRATION
        from core.services.manual_seller_dispatch import manual_consent_status
        consent=SellerContactConsent.objects.create(seller=self.seller,phone_normalized=self.seller.whatsapp,
            channel='whatsapp',purpose='marketing',status='granted',source=CONTACT_CONSENT_SOURCE_REGISTRATION,
            consent_text_version='seller_registration_whatsapp_v1',consented_at=timezone.now())
        response=self.client.get(self.picker()+'?scope=all&consent=granted')
        self.assertContains(response,self.seller.name)
        self.client.post(self.picker()+'?consent=granted',{'action':'prepare','seller_ids':[self.seller.pk]})
        self.assertIn('legacy_consent:',RequestDispatch.objects.get().permission_evidence)
        self.assertEqual(SellerContactConsent.objects.count(),1)
        consent.status='revoked';consent.revoked_at=timezone.now();consent.save(update_fields=['status','revoked_at'])
        self.assertEqual(manual_consent_status(self.seller),'revoked')
        self.assertEqual(self.client.get(self.picker()+'?consent=granted').context['total'],0)

    def test_unknown_advertising_consent_does_not_grant_requests_and_explicit_opt_out_wins(self):
        from core.models import SellerContactConsent, SellerChannelConnection
        from core.services.manual_seller_dispatch import manual_consent_status
        SellerContactConsent.objects.create(seller=self.seller,phone_normalized=self.seller.whatsapp,
            channel='whatsapp',purpose='marketing',status='granted',
            consent_text_version='advertising_only',consented_at=timezone.now())
        self.assertEqual(manual_consent_status(self.seller),'')
        SellerChannelConnection.objects.create(seller=self.seller,whatsapp_consent=True)
        SellerContactConsent.objects.create(seller=self.seller,phone_normalized=self.seller.whatsapp,
            channel='whatsapp',purpose='buyer_requests',status='revoked',revoked_at=timezone.now())
        self.assertEqual(self.client.get(self.picker()+'?consent=granted').context['total'],0)

    def test_full_picker_query_count_does_not_grow_with_each_seller(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from django.http import QueryDict
        from control_panel.manual_processing import recipient_rows
        with CaptureQueriesContext(connection) as small:
            recipient_rows(self.req,QueryDict('scope=all'))
        Seller.objects.bulk_create([Seller(name=f'Профиль {n}',city='Актобе',transport_type='car',
            whatsapp=f'7701001{n:04}',sells_new_parts=True,all_brands=True,all_categories=True)
            for n in range(500)])
        with CaptureQueriesContext(connection) as large:
            rows=recipient_rows(self.req,QueryDict('scope=all'))
        self.assertEqual(len(rows),501)
        self.assertLessEqual(len(large),len(small)+1)
        self.assertLessEqual(len(large),10)
        print(f'MANUAL_PICKER_QUERIES one={len(small)} five_hundred={len(large)}')

    def test_prepare_failure_stays_at_visible_feedback_and_opens_permission_form(self):
        response=self.client.post(self.picker(),{'action':'prepare','seller_ids':[self.seller.pk]})
        self.assertEqual(response.url,self.review()+'#selection-feedback')
        response=self.client.get(response.url)
        self.assertContains(response,'Нужно согласие продавца')
        self.assertContains(response,'не найдено сохранённое согласие')
        self.assertContains(response,'id="one-time-permission"')
        html=response.content.decode()
        self.assertNotIn('<table', html)
        self.assertEqual(html.count('name="action" value="prepare"'),1)
        self.assertFalse(RequestDispatch.objects.exists())

    def test_prepare_success_goes_to_ready_messages_and_sent_history_is_separate(self):
        from core.models import SellerChannelConnection
        SellerChannelConnection.objects.create(seller=self.seller,whatsapp_consent=True)
        response=self.client.post(self.picker(),{'action':'prepare','seller_ids':[self.seller.pk]})
        self.assertEqual(response.url,self.review()+'#send')
        response=self.client.get(self.review())
        self.assertEqual(response.context['prepared_count'],1)
        self.assertContains(response,'Отправить в WhatsApp')
        dispatch=RequestDispatch.objects.get()
        self.client.post(self.picker(),{'action':'confirm_sent','dispatch_id':dispatch.pk,'did_send':'yes'})
        response=self.client.get(self.review())
        self.assertEqual(response.context['prepared_count'],0)
        self.assertContains(response,'История отправок: 1')
        self.assertNotContains(response,'>Отправить в WhatsApp</button>')

    def test_immediate_selection_persists_across_filters_without_dispatch(self):
        key = f'manual_recipients:{self.req.pk}'
        r = self.client.post(self.picker(), {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'yes'})
        self.assertEqual(r.json(), {'ok':True, 'selected_count':1})
        self.assertEqual(self.client.session[key], [self.seller.pk])
        r = self.client.get(self.picker()+'?tab=selected&city=НетТакогоГорода')
        self.assertContains(r, self.seller.name)
        self.assertTrue(r.context['page'][0]['selected'])
        self.assertFalse(RequestDispatch.objects.exists())
        self.assertNotContains(self.client.get(self.picker()), 'Подготовить сообщения')
        self.assertContains(self.client.get(self.review()), 'Подготовить сообщения')
        self.client.post(self.picker(), {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'no'})
        self.assertEqual(self.client.session[key], [])

    def test_review_requires_explicit_outside_decision_and_saves_audit(self):
        self.seller.city = 'Алматы'; self.seller.save(update_fields=['city'])
        r = self.client.post(self.picker()+'?scope=all', {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'yes'})
        self.assertTrue(r.json()['ok'])
        self.client.post(self.review(), {'action':'prepare', 'one_time_permission':'yes'})
        self.assertFalse(RequestDispatch.objects.exists())
        self.client.post(self.review(), {'action':'prepare', 'one_time_permission':'yes', 'allow_outside':'yes'})
        self.assertTrue(RequestDispatch.objects.filter(seller=self.seller, status='prepared').exists())
        self.assertTrue(LogEntry.objects.filter(change_message=f'manual_territory_override:{self.seller.pk}:Алматы').exists())
        self.req.refresh_from_db(); self.assertEqual(self.req.search_scope, 'city')

    def test_toggle_cannot_select_sent_or_paused_seller_and_requires_csrf(self):
        self.seller.is_paused = True; self.seller.save(update_fields=['is_paused'])
        r = self.client.post(self.picker(), {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'yes'})
        self.assertEqual(r.status_code, 400)
        other = Client(enforce_csrf_checks=True); other.force_login(self.user)
        self.assertEqual(other.post(self.picker(), {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'yes'}).status_code,403)

    def test_stale_selected_store_remains_visible_and_can_be_removed(self):
        self.client.post(self.picker(), {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'yes'})
        self.seller.transport_type='truck'; self.seller.save(update_fields=['transport_type'])
        response=self.client.get(self.picker()+'?tab=selected')
        self.assertContains(response,self.seller.name)
        self.assertContains(response,'Тип транспорта не совпадает.')
        self.assertTrue(response.context['page'][0]['selected'])
        self.client.post(self.picker(), {'action':'toggle', 'seller_id':self.seller.pk, 'checked':'no'})
        self.assertEqual(self.client.session[f'manual_recipients:{self.req.pk}'],[])
