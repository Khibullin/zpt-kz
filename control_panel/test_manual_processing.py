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
        self.assertContains(response, 'Уточнить в WhatsApp')
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
        self.assertContains(self.client.get(self.picker()), 'Отправить в WhatsApp')
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

    def picker(self):
        return reverse('control_panel:parts_request_sellers', args=[self.req.pk])

    def test_picker_includes_universal_receiver_channel_consent_and_catalog_reason(self):
        from django.utils import timezone
        from datetime import timedelta
        from core.models import SellerChannelConnection
        owner = Seller.objects.create(name='AG Parts', whatsapp='77011013033', city='Алматы',
            transport_type='truck', is_active=True, is_test_seller=True,
            test_all_requests_since=self.req.created_at - timedelta(days=1))
        SellerChannelConnection.objects.create(seller=owner, whatsapp_consent=True)
        catalog = Seller.objects.create(name='Каталог Актобе', city='Актобе', transport_type='car',
            whatsapp='77011013034', catalog_only=True, all_brands=True, all_categories=True, sells_new_parts=True)
        response = self.client.get(self.picker())
        self.assertContains(response, 'AG Parts')
        self.assertContains(response, 'Есть согласие')
        self.assertContains(response, 'владелец ещё не подключён')
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
        self.assertNotContains(self.client.get(self.picker()+'?consent=granted'), 'Каталог Актобе')

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
        response=self.client.get(self.picker())
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
