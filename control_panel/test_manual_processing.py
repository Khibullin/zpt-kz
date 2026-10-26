from django.contrib.auth.models import Permission, User
from django.contrib.admin.models import LogEntry
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from core.models import Request, Seller, RequestDispatch, Match


@override_settings(ALLOWED_HOSTS=['*'], PUBLIC_BASE_URL='https://zpt.kz')
class ManualWorkspaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('operator', is_staff=True)
        self.user.user_permissions.add(Permission.objects.get(codename='change_request', content_type__app_label='core'))
        self.req = Request.objects.create(source='home_short', transport_type='car', brand='BMW',
            model='5 Series', year=1993, city='Актобе', category='Топливная система',
            search_scope='city', description='Форсунки', phone='77072812731')
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
        self.assertContains(response, 'BMW Актобе')
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
        self.assertContains(self.client.get(self.url), 'Открыть WhatsApp с заявкой')
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
