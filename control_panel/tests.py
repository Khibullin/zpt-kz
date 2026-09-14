from datetime import timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_SOURCE_REGISTRATION,
    CONTACT_CONSENT_STATUS_GRANTED,
    Match,
    Request,
    RequestDispatch,
    Seller,
    SellerContactConsent,
    SellerRequestPageEvent,
)
from core.services.seller_request_access import create_seller_request_access
from control_panel.selectors.common import (
    SELLER_EVENT_HISTORY_LIMIT,
    SELLER_MATCH_HISTORY_LIMIT,
    WA_LOG_HISTORY_LIMIT,
)
from service_requests.models import (
    Service,
    ServiceMatch,
    ServiceRequest,
    ServiceSeller,
    ServiceWhatsAppMessageLog,
)

CONTROL_LIST_URLS = (
    '/control/',
    '/control/requests/parts/',
    '/control/requests/services/',
    '/control/partners/sellers/',
    '/control/partners/services/',
)
META_ERROR_SECRET = 'SECRET_META_PAYLOAD_CONTROL_XYZ'
META_JSON_SECRET = '{"error":"SECRET_JSON_CONTROL_XYZ"}'


def _staff(**kwargs):
    defaults = {
        'username': 'control-staff',
        'password': 'secret-pass',
        'is_staff': True,
        'is_superuser': False,
    }
    defaults.update(kwargs)
    password = defaults.pop('password')
    user = User.objects.create_user(password=password, **defaults)
    return user, password


def _seller(**kwargs):
    defaults = {
        'name': 'Test Seller',
        'whatsapp': '77015550001',
        'city': 'Алматы',
        'is_active': True,
        'receive_requests': True,
        'is_paused': False,
        'category': 'Тормоза',
    }
    defaults.update(kwargs)
    return Seller.objects.create(**defaults)


def _request(**kwargs):
    defaults = {
        'transport_type': 'car',
        'brand': 'Toyota',
        'model': 'Camry',
        'category': 'Тормоза',
        'city': 'Алматы',
        'phone': '77019990011',
        'description': 'Нужен цилиндр',
        'status': 'sent',
    }
    defaults.update(kwargs)
    return Request.objects.create(**defaults)


@override_settings(ALLOWED_HOSTS=['*'], ROOT_URLCONF='backend.urls')
class ControlPanelTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST='zpt.kz')
        self.staff, self.staff_password = _staff()

    def _login_staff(self):
        self.client.login(username=self.staff.username, password=self.staff_password)

    def test_anonymous_is_redirected_to_admin_login(self):
        response = self.client.get('/control/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)
        self.assertIn('next=', response.url)

    def test_non_staff_user_cannot_open_control(self):
        User.objects.create_user('plain', password='secret-pass', is_staff=False)
        self.client.login(username='plain', password='secret-pass')
        response = self.client.get('/control/')
        self.assertNotEqual(response.status_code, 200)
        self.assertIn(response.status_code, (302, 403))

    def test_staff_and_superuser_can_open_control(self):
        self._login_staff()
        response = self.client.get('/control/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ZPT.KZ Control')
        self.client.logout()
        User.objects.create_superuser('root', 'root@example.com', 'secret-pass')
        self.client.login(username='root', password='secret-pass')
        response = self.client.get('/control/')
        self.assertEqual(response.status_code, 200)

    def test_overview_kpis_and_periods(self):
        self._login_staff()
        seller = _seller()
        other = _seller(name='Second', whatsapp='77015550002')
        old = _request(brand='OldBrand')
        old.created_at = timezone.now() - timedelta(days=40)
        old.save(update_fields=['created_at'])
        mid = _request(brand='MidBrand', phone='77019990033')
        mid.created_at = timezone.now() - timedelta(days=10)
        mid.save(update_fields=['created_at'])
        current = _request(brand='NewBrand', phone='77019990022')
        RequestDispatch.objects.create(
            request=current,
            seller=seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=timezone.now(),
            sent_at=timezone.now(),
        )
        access = create_seller_request_access(request=current, seller=seller)
        other_access = create_seller_request_access(request=current, seller=other)
        third = _seller(name='Third', whatsapp='77015550003')
        third_access = create_seller_request_access(request=current, seller=third)
        for event_type in ('page_open', 'whatsapp_click', 'call_click', 'marketing_consent_yes'):
            SellerRequestPageEvent.objects.create(
                access=access,
                request=current,
                seller=seller,
                event_type=event_type,
            )
        SellerRequestPageEvent.objects.create(
            access=access,
            request=current,
            seller=seller,
            event_type='out_of_stock',
        )
        SellerRequestPageEvent.objects.create(
            access=other_access,
            request=current,
            seller=other,
            event_type='cannot_fulfill',
        )
        SellerRequestPageEvent.objects.create(
            access=third_access,
            request=current,
            seller=third,
            event_type='marketing_consent_no',
        )
        ServiceRequest.objects.create(
            service_type='sto',
            city='Алматы',
            phone='77012223344',
        )
        today = self.client.get('/control/?period=today')
        week = self.client.get('/control/?period=7d')
        month = self.client.get('/control/?period=30d')
        for response in (today, week, month):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Заявки на запчасти')
            self.assertContains(response, 'Отправлено предложений продавцам')
            self.assertContains(response, 'Переходы в WhatsApp')
            self.assertContains(response, 'Текущий backlog, не зависит от выбранного периода')
        kpis = {item.key: item.value for item in week.context['kpis']}
        self.assertEqual(kpis['parts_created'], 1)
        self.assertEqual(kpis['services_created'], 1)
        self.assertEqual(kpis['offers_sent'], 1)
        self.assertEqual(kpis['page_open'], 1)
        self.assertEqual(kpis['whatsapp_click'], 1)
        self.assertEqual(kpis['call_click'], 1)
        self.assertEqual(kpis['out_of_stock'], 1)
        self.assertEqual(kpis['cannot_fulfill'], 1)
        self.assertEqual(kpis['consent_yes'], 1)
        self.assertEqual(kpis['consent_no'], 1)
        today_kpis = {item.key: item.value for item in today.context['kpis']}
        month_kpis = {item.key: item.value for item in month.context['kpis']}
        self.assertEqual(today_kpis['parts_created'], 1)
        self.assertEqual(month_kpis['parts_created'], 2)
        recent_titles = [row.title for row in week.context['recent_parts']]
        self.assertTrue(any('NewBrand' in title for title in recent_titles))
        self.assertFalse(any('OldBrand' in title for title in recent_titles))
        self.assertFalse(any('MidBrand' in title for title in recent_titles))
        month_titles = [row.title for row in month.context['recent_parts']]
        self.assertTrue(any('MidBrand' in title for title in month_titles))

    def test_parts_list_filters_search_and_hides_phone(self):
        self._login_staff()
        seller = _seller()
        req = _request()
        other = _request(brand='Honda', city='Астана', phone='77018887766', category='Фильтры')
        Match.objects.create(request=req, seller=seller, status='sent', sent_at=timezone.now())
        access = create_seller_request_access(request=req, seller=seller)
        SellerRequestPageEvent.objects.create(
            access=access, request=req, seller=seller, event_type='page_open'
        )
        response = self.client.get('/control/requests/parts/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertNotIn('77019990011', body)
        self.assertNotIn('77018887766', body)
        self.assertContains(response, 'Toyota')
        filtered = self.client.get('/control/requests/parts/?q=Honda&city=Астана')
        self.assertContains(filtered, 'Honda')
        self.assertNotContains(filtered, 'Toyota')
        opened = self.client.get('/control/requests/parts/?opened=yes')
        self.assertContains(opened, f'№{req.pk}'.replace('№', ''))
        self.assertContains(opened, str(req.pk))
        number = self.client.get(f'/control/requests/parts/?request_id={req.pk}')
        self.assertContains(number, 'Camry')
        self.assertNotContains(number, 'Honda')

    def test_parts_detail_events_mask_phone_and_hide_token(self):
        self._login_staff()
        seller = _seller()
        req = _request()
        Match.objects.create(request=req, seller=seller, status='sent', sent_at=timezone.now())
        access = create_seller_request_access(request=req, seller=seller)
        SellerRequestPageEvent.objects.create(
            access=access, request=req, seller=seller, event_type='page_open'
        )
        SellerRequestPageEvent.objects.create(
            access=access, request=req, seller=seller, event_type='whatsapp_click'
        )
        url = reverse('control_panel:parts_request_detail', args=[req.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('7701•••11', body)
        self.assertNotIn('77019990011', body)
        self.assertNotIn(access.token, body)
        self.assertNotIn(str(req.access_token), body)
        self.assertContains(response, 'Открыл заявку')
        self.assertContains(response, 'Перешёл в WhatsApp')
        self.assertContains(response, f'#{access.pk}')

    def test_seller_list_and_rh_like_consent_card(self):
        self._login_staff()
        rh = _seller(name='RH', whatsapp='77015550999', city='Алматы')
        SellerContactConsent.objects.create(
            seller=rh,
            phone_normalized='77015550999',
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            source=CONTACT_CONSENT_SOURCE_REGISTRATION,
            consent_text_version='seller_registration_whatsapp_v1',
            consented_at=timezone.now(),
        )
        listing = self.client.get('/control/partners/sellers/?consent=granted')
        self.assertContains(listing, 'RH')
        self.assertContains(listing, 'Разрешено')
        detail = self.client.get(
            reverse('control_panel:seller_detail', args=[rh.pk])
        )
        self.assertContains(detail, 'granted')
        self.assertContains(detail, 'registration')
        self.assertContains(detail, 'seller_registration_whatsapp_v1')
        self.assertContains(detail, '/admin/core/seller/')

    def test_service_requests_use_existing_models(self):
        self._login_staff()
        service = Service.objects.create(name='Диагностика')
        sto = ServiceSeller.objects.create(
            name='Auto STO',
            whatsapp='77016660001',
            password='unused',
            city='Алматы',
            seller_type='sto',
        )
        sto.services.add(service)
        req = ServiceRequest.objects.create(
            service_type='sto',
            brand='Kia',
            model='Rio',
            city='Алматы',
            phone='77017770001',
        )
        req.services.add(service)
        ServiceMatch.objects.create(request=req, seller=sto, status='sent')
        ServiceWhatsAppMessageLog.objects.create(
            seller=sto,
            request=req,
            phone='77016660001',
            message_type='seller_request',
            status='failed',
            error_text=META_ERROR_SECRET,
            response_json=META_JSON_SECRET,
        )
        listing = self.client.get('/control/requests/services/')
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, 'Диагностика')
        self.assertContains(listing, 'Нет данных')
        body = listing.content.decode('utf-8')
        self.assertNotIn('77017770001', body)
        detail = self.client.get(
            reverse('control_panel:service_request_detail', args=[req.pk])
        )
        self.assertContains(detail, '7701•••01')
        self.assertContains(detail, 'Ошибка отправки')
        detail_body = detail.content.decode('utf-8')
        self.assertNotIn('77017770001', detail_body)
        self.assertNotIn(META_ERROR_SECRET, detail_body)
        self.assertNotIn(META_JSON_SECRET, detail_body)
        self.assertNotIn('77016660001', detail_body)
        sto_list = self.client.get('/control/partners/services/')
        self.assertContains(sto_list, 'Auto STO')
        sto_detail = self.client.get(reverse('control_panel:sto_detail', args=[sto.pk]))
        self.assertContains(sto_detail, 'Диагностика')
        self.assertContains(sto_detail, 'Ошибка отправки')
        sto_body = sto_detail.content.decode('utf-8')
        self.assertNotIn(sto.whatsapp, sto_body)
        self.assertNotIn('77017770001', sto_body)
        self.assertNotIn(META_ERROR_SECRET, sto_body)
        self.assertNotIn(META_JSON_SECRET, sto_body)
        self.assertNotContains(sto_detail, 'password')
        self.assertNotContains(sto_detail, 'error_text')
        self.assertNotContains(detail, 'error_text')

    def test_pagination_keeps_filters(self):
        self._login_staff()
        for index in range(51):
            _request(
                brand=f'Brand{index}',
                phone=f'7701999{index:04d}'[:11].ljust(11, '0'),
                city='Алматы',
            )
        from urllib.parse import unquote

        response = self.client.get('/control/requests/parts/?city=Алматы&page=2')
        self.assertEqual(response.status_code, 200)
        body = unquote(response.content.decode('utf-8'))
        self.assertIn('city=', body)
        self.assertIn('Алматы', body)
        self.assertIn('page=1', body)
        self.assertContains(response, 'Стр. 2 из 2')

    def test_pages_have_noindex_and_private_cache(self):
        self._login_staff()
        seller = _seller(whatsapp='77015550901')
        req = _request(phone='77019990901')
        access = create_seller_request_access(request=req, seller=seller)
        sto = ServiceSeller.objects.create(
            name='Cache STO',
            whatsapp='77016660901',
            password='unused',
            city='Алматы',
            seller_type='sto',
        )
        sreq = ServiceRequest.objects.create(
            service_type='sto', city='Алматы', phone='77017770901'
        )
        urls = list(CONTROL_LIST_URLS) + [
            reverse('control_panel:parts_request_detail', args=[req.pk]),
            reverse('control_panel:service_request_detail', args=[sreq.pk]),
            reverse('control_panel:seller_detail', args=[seller.pk]),
            reverse('control_panel:sto_detail', args=[sto.pk]),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.assertContains(response, 'noindex, nofollow')
            self.assertEqual(response.get('X-Robots-Tag'), 'noindex, nofollow')
            self.assertNotIn(access.token, response.content.decode('utf-8'))

    def test_nested_urls_require_staff(self):
        seller = _seller(whatsapp='77015550902')
        req = _request(phone='77019990902')
        sto = ServiceSeller.objects.create(
            name='Nested STO',
            whatsapp='77016660902',
            password='unused',
            city='Алматы',
            seller_type='sto',
        )
        sreq = ServiceRequest.objects.create(
            service_type='sto', city='Алматы', phone='77017770902'
        )
        urls = list(CONTROL_LIST_URLS) + [
            reverse('control_panel:parts_request_detail', args=[req.pk]),
            reverse('control_panel:service_request_detail', args=[sreq.pk]),
            reverse('control_panel:seller_detail', args=[seller.pk]),
            reverse('control_panel:sto_detail', args=[sto.pk]),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertNotEqual(response.status_code, 200, url)
            self.assertIn('/admin/login/', response.url)
        User.objects.create_user('plain-nested', password='secret-pass', is_staff=False)
        self.client.login(username='plain-nested', password='secret-pass')
        for url in urls:
            response = self.client.get(url)
            self.assertNotEqual(response.status_code, 200, url)
            self.assertIn(response.status_code, (302, 403))
        self.client.logout()
        self._login_staff()
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
        self.client.logout()
        User.objects.create_superuser('root-nested', 'root2@example.com', 'secret-pass')
        self.client.login(username='root-nested', password='secret-pass')
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_get_does_not_change_business_records(self):
        self._login_staff()
        seller = _seller(whatsapp='77015550903')
        req = _request(phone='77019990903')
        Match.objects.create(request=req, seller=seller, status='sent', sent_at=timezone.now())
        access = create_seller_request_access(request=req, seller=seller)
        SellerRequestPageEvent.objects.create(
            access=access, request=req, seller=seller, event_type='page_open'
        )
        sto = ServiceSeller.objects.create(
            name='Immutable STO',
            whatsapp='77016660903',
            password='unused',
            city='Алматы',
            seller_type='sto',
        )
        sreq = ServiceRequest.objects.create(
            service_type='sto', city='Алматы', phone='77017770903'
        )
        ServiceMatch.objects.create(request=sreq, seller=sto, status='sent')
        snapshot = {
            'requests': Request.objects.count(),
            'sellers': Seller.objects.count(),
            'matches': Match.objects.count(),
            'events': SellerRequestPageEvent.objects.count(),
            'service_requests': ServiceRequest.objects.count(),
            'service_matches': ServiceMatch.objects.count(),
            'wa_logs': ServiceWhatsAppMessageLog.objects.count(),
            'consents': SellerContactConsent.objects.count(),
        }
        urls = list(CONTROL_LIST_URLS) + [
            reverse('control_panel:parts_request_detail', args=[req.pk]),
            reverse('control_panel:service_request_detail', args=[sreq.pk]),
            reverse('control_panel:seller_detail', args=[seller.pk]),
            reverse('control_panel:sto_detail', args=[sto.pk]),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(Request.objects.count(), snapshot['requests'])
        self.assertEqual(Seller.objects.count(), snapshot['sellers'])
        self.assertEqual(Match.objects.count(), snapshot['matches'])
        self.assertEqual(SellerRequestPageEvent.objects.count(), snapshot['events'])
        self.assertEqual(ServiceRequest.objects.count(), snapshot['service_requests'])
        self.assertEqual(ServiceMatch.objects.count(), snapshot['service_matches'])
        self.assertEqual(ServiceWhatsAppMessageLog.objects.count(), snapshot['wa_logs'])
        self.assertEqual(SellerContactConsent.objects.count(), snapshot['consents'])
        self.assertEqual(
            SellerRequestPageEvent.objects.get(pk=access.page_events.first().pk).event_type,
            'page_open',
        )

    def test_missing_cards_return_safe_404(self):
        self._login_staff()
        urls = [
            '/control/requests/parts/999999/',
            '/control/requests/services/999999/',
            '/control/partners/sellers/999999/',
            '/control/partners/services/999999/',
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 404, url)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            body = response.content.decode('utf-8')
            self.assertIn('noindex, nofollow', body)
            self.assertNotIn('Traceback', body)
            self.assertNotIn(META_ERROR_SECRET, body)
            self.assertNotIn('SECRET_KEY', body)

    def test_logout_requires_post_and_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True, HTTP_HOST='zpt.kz')
        self.assertTrue(
            csrf_client.login(username=self.staff.username, password=self.staff_password)
        )
        page = csrf_client.get('/control/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'csrfmiddlewaretoken')
        forbidden = csrf_client.post(reverse('admin:logout'))
        self.assertEqual(forbidden.status_code, 403)
        self.assertTrue(csrf_client.session.get('_auth_user_id'))
        token = page.context['csrf_token']
        logged_out = csrf_client.post(
            reverse('admin:logout'),
            {'csrfmiddlewaretoken': str(token)},
        )
        self.assertIn(logged_out.status_code, (200, 302))
        self.assertFalse(csrf_client.session.get('_auth_user_id'))

    def test_history_limits_are_sql_bounded(self):
        self._login_staff()
        seller = _seller(whatsapp='77015550910', name='History Seller')
        now = timezone.now()
        request_ids = []
        for index in range(110):
            req = _request(
                brand=f'Hist{index:03d}',
                phone=f'77015{index:06d}'[:11],
            )
            Match.objects.create(
                request=req, seller=seller, status='sent', sent_at=now
            )
            access = create_seller_request_access(request=req, seller=seller)
            event = SellerRequestPageEvent.objects.create(
                access=access,
                request=req,
                seller=seller,
                event_type='page_open',
            )
            event.created_at = now - timedelta(minutes=110 - index)
            event.save(update_fields=['created_at'])
            match = Match.objects.get(request=req, seller=seller)
            match.created_at = now - timedelta(minutes=110 - index)
            match.save(update_fields=['created_at'])
            request_ids.append(req.pk)
        sto = ServiceSeller.objects.create(
            name='History STO',
            whatsapp='77016660910',
            password='unused',
            city='Алматы',
            seller_type='sto',
        )
        sreq = ServiceRequest.objects.create(
            service_type='sto', city='Алматы', phone='77017770910'
        )
        for index in range(110):
            log = ServiceWhatsAppMessageLog.objects.create(
                seller=sto,
                request=sreq,
                phone='77016660910',
                message_type='seller_request',
                status='failed' if index == 109 else 'sent',
                error_text=META_ERROR_SECRET + str(index),
                response_json=META_JSON_SECRET,
            )
            log.created_at = now - timedelta(minutes=110 - index)
            log.save(update_fields=['created_at'])

        seller_url = reverse('control_panel:seller_detail', args=[seller.pk])
        with CaptureQueriesContext(connection) as ctx:
            seller_response = self.client.get(seller_url)
        self.assertEqual(seller_response.status_code, 200)
        self.assertLessEqual(len(ctx), 20)
        self.assertEqual(len(seller_response.context['events']), SELLER_EVENT_HISTORY_LIMIT)
        self.assertEqual(len(seller_response.context['requests']), SELLER_MATCH_HISTORY_LIMIT)
        newest_event_ids = list(reversed(request_ids))[:SELLER_EVENT_HISTORY_LIMIT]
        shown_event_ids = [row['request_id'] for row in seller_response.context['events']]
        self.assertEqual(shown_event_ids, newest_event_ids)
        seller_body = seller_response.content.decode('utf-8')
        self.assertIn(f'/control/requests/parts/{request_ids[-1]}/', seller_body)
        self.assertNotIn(f'/control/requests/parts/{request_ids[0]}/', seller_body)
        self.assertEqual(seller_response.context['opened'], 110)

        sto_url = reverse('control_panel:sto_detail', args=[sto.pk])
        service_url = reverse('control_panel:service_request_detail', args=[sreq.pk])
        with CaptureQueriesContext(connection) as many_ctx:
            sto_response = self.client.get(sto_url)
            service_response = self.client.get(service_url)
        self.assertEqual(sto_response.status_code, 200)
        self.assertEqual(service_response.status_code, 200)
        self.assertLessEqual(len(many_ctx), 30)
        self.assertEqual(len(sto_response.context['logs']), WA_LOG_HISTORY_LIMIT)
        self.assertEqual(len(service_response.context['logs']), WA_LOG_HISTORY_LIMIT)
        self.assertEqual(
            sto_response.context['logs'][0]['status_label'], 'Ошибка отправки'
        )
        for response in (sto_response, service_response):
            body = response.content.decode('utf-8')
            self.assertNotIn(META_ERROR_SECRET, body)
            self.assertNotIn(META_JSON_SECRET, body)
            self.assertNotIn('77016660910', body)

    def test_query_counts_stay_bounded(self):
        self._login_staff()
        seller = _seller()
        req = _request()
        Match.objects.create(request=req, seller=seller, status='sent', sent_at=timezone.now())
        access = create_seller_request_access(request=req, seller=seller)
        SellerRequestPageEvent.objects.create(
            access=access, request=req, seller=seller, event_type='page_open'
        )
        Service.objects.create(name='Диагностика')
        sto = ServiceSeller.objects.create(
            name='Auto STO',
            whatsapp='77016660099',
            password='unused',
            city='Алматы',
            seller_type='sto',
        )
        sreq = ServiceRequest.objects.create(
            service_type='sto', city='Алматы', phone='77017770099'
        )
        ServiceMatch.objects.create(request=sreq, seller=sto, status='sent')
        bounds = {
            '/control/': 25,
            '/control/requests/parts/': 20,
            reverse('control_panel:parts_request_detail', args=[req.pk]): 15,
            '/control/requests/services/': 15,
            reverse('control_panel:service_request_detail', args=[sreq.pk]): 15,
            '/control/partners/sellers/': 18,
            reverse('control_panel:seller_detail', args=[seller.pk]): 18,
            '/control/partners/services/': 12,
            reverse('control_panel:sto_detail', args=[sto.pk]): 15,
        }
        for url, limit in bounds.items():
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertLessEqual(
                len(ctx),
                limit,
                f'{url} used {len(ctx)} queries, limit {limit}',
            )

    def test_existing_public_and_admin_routes_still_work(self):
        response = self.client.get('/admin/login/')
        self.assertEqual(response.status_code, 200)
        home = self.client.get('/')
        self.assertIn(home.status_code, (200, 302))
        cabinet = self.client.get('/request-parts/cabinet/')
        self.assertEqual(cabinet.status_code, 200)
        unknown = self.client.get('/sr/unknown-control-token/')
        self.assertEqual(unknown.status_code, 404)
