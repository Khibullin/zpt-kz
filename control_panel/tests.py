from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from control_panel.display import normalize_sto_sort, summarize_names

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
    '/control/kaspi/products/',
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
            self.assertContains(response, 'Отправлено уведомлений продавцам')
            self.assertContains(response, 'Открыто заявок продавцами')
            self.assertContains(response, 'Звонки покупателям')
            self.assertContains(response, 'Согласились на предложения')
            self.assertNotContains(response, 'page_open')
            self.assertNotContains(response, 'whatsapp_click')
            self.assertNotContains(response, 'RequestDispatch')
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
        self.assertNotContains(week, 'Нет статуса заявки')
        self.assertContains(week, 'Последние заявки СТО')
        self.assertContains(week, 'Подобрано СТО')
        self.assertContains(week, 'Отправлено уведомлений')
        service_row = week.context['recent_services'][0]
        self.assertFalse(hasattr(service_row, 'status'))
        self.assertEqual(service_row.matched, 0)
        self.assertEqual(service_row.notified, 0)

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
        SellerRequestPageEvent.objects.create(
            access=access, request=req, seller=seller, event_type='marketing_consent_yes'
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
        self.assertNotIn(f'#{access.pk}', body)
        self.assertContains(response, 'Разрешил здесь')
        self.assertContains(response, 'Создано ссылок продавцам')
        self.assertContains(response, 'Звонок')
        self.assertNotContains(response, '>Call<')
        self.assertRegex(body, r'\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}')
        events = list(response.context['events'])
        self.assertGreaterEqual(len(events), 2)
        self.assertGreaterEqual(events[0].created_at, events[1].created_at)

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
        self.assertContains(listing, 'Отправлено заявок')
        self.assertContains(listing, 'Нет в наличии')
        detail = self.client.get(
            reverse('control_panel:seller_detail', args=[rh.pk])
        )
        detail_body = detail.content.decode('utf-8')
        self.assertContains(detail, 'Разрешено')
        self.assertContains(detail, 'Регистрация')
        self.assertContains(detail, 'Согласие на предложения')
        self.assertNotIn('granted', detail_body)
        self.assertNotIn('registration', detail_body)
        self.assertNotIn('seller_registration_whatsapp_v1', detail_body)
        self.assertContains(detail, '/admin/core/seller/')
        req = _request(phone='77019950999')
        Match.objects.create(request=req, seller=rh, status='sent', sent_at=timezone.now())
        create_seller_request_access(request=req, seller=rh)
        parts = self.client.get(reverse('control_panel:parts_request_detail', args=[req.pk]))
        self.assertContains(parts, 'Разрешено ранее')
        junk_sort = self.client.get('/control/partners/sellers/?sort=;drop table')
        self.assertEqual(junk_sort.status_code, 200)
        self.assertEqual(junk_sort.context['filters']['sort'], 'name')

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
        self.assertContains(listing, 'Статусы и ответы СТО пока не собираются')
        self.assertContains(
            listing,
            f'href="/control/requests/services/{req.pk}/"',
            html=False,
        )
        body = listing.content.decode('utf-8')
        self.assertNotIn('77017770001', body)
        self.assertNotIn('ServiceMatch', body)
        self.assertNotRegex(body, r'\bsent\b')
        detail = self.client.get(
            reverse('control_panel:service_request_detail', args=[req.pk])
        )
        self.assertContains(detail, '7701•••01')
        self.assertContains(detail, 'Ошибка отправки')
        self.assertContains(detail, 'Уведомление о заявке')
        detail_body = detail.content.decode('utf-8')
        self.assertNotIn('seller_request', detail_body)
        self.assertNotIn('77017770001', detail_body)
        self.assertNotIn(META_ERROR_SECRET, detail_body)
        self.assertNotIn(META_JSON_SECRET, detail_body)
        self.assertNotIn('77016660001', detail_body)
        sto_list = self.client.get('/control/partners/services/')
        self.assertContains(sto_list, 'Auto STO')
        sto_detail = self.client.get(reverse('control_panel:sto_detail', args=[sto.pk]))
        self.assertContains(sto_detail, 'Диагностика')
        self.assertContains(sto_detail, 'Ошибка отправки')
        self.assertContains(sto_detail, 'Заявки, направленные СТО')
        self.assertContains(sto_detail, 'История WhatsApp-уведомлений')
        self.assertContains(sto_detail, 'Учёт ответов СТО пока не подключён')
        self.assertNotContains(sto_detail, 'Ответы:')
        self.assertNotContains(sto_detail, 'Пауза')
        sto_body = sto_detail.content.decode('utf-8')
        self.assertNotIn(sto.whatsapp, sto_body)
        self.assertNotIn('77017770001', sto_body)
        self.assertNotIn(META_ERROR_SECRET, sto_body)
        self.assertNotIn(META_JSON_SECRET, sto_body)
        self.assertNotIn('seller_request', sto_body)
        self.assertNotIn('ServiceMatch', sto_body)
        self.assertNotRegex(sto_body, r'\bsent\b')
        self.assertContains(sto_detail, 'Уведомление о заявке')
        self.assertNotContains(sto_detail, 'password')
        self.assertNotContains(sto_detail, 'error_text')
        self.assertNotContains(sto_detail, 'response_json')
        self.assertNotContains(detail, 'error_text')
        self.assertNotContains(detail, 'ServiceMatch')

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
            '/control/requests/services/': 18,
            reverse('control_panel:service_request_detail', args=[sreq.pk]): 15,
            '/control/partners/sellers/': 18,
            reverse('control_panel:seller_detail', args=[seller.pk]): 20,
            '/control/partners/services/': 16,
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

    def test_seller_card_shows_reactions_without_technical_ids(self):
        self._login_staff()
        seller = _seller(name='Reaction Seller', whatsapp='77015551111')
        opened = _request(phone='77019991111', brand='Toyota', model='Camry')
        stock = _request(phone='77019991112', brand='Honda', model='Civic')
        silent = _request(phone='77019991113', brand='Kia', model='Rio')
        Match.objects.create(
            request=opened, seller=seller, status='sent', sent_at=timezone.now()
        )
        Match.objects.create(
            request=stock, seller=seller, status='sent', sent_at=timezone.now()
        )
        Match.objects.create(
            request=silent, seller=seller, status='failed', sent_at=timezone.now()
        )
        opened_access = create_seller_request_access(request=opened, seller=seller)
        stock_access = create_seller_request_access(request=stock, seller=seller)
        create_seller_request_access(request=silent, seller=seller)
        for event_type in ('page_open', 'whatsapp_click', 'call_click'):
            SellerRequestPageEvent.objects.create(
                access=opened_access,
                request=opened,
                seller=seller,
                event_type=event_type,
            )
        SellerRequestPageEvent.objects.create(
            access=stock_access,
            request=stock,
            seller=seller,
            event_type='out_of_stock',
        )
        cannot = _request(phone='77019991114', brand='Mazda', model='6')
        Match.objects.create(
            request=cannot, seller=seller, status='sent', sent_at=timezone.now()
        )
        cannot_access = create_seller_request_access(request=cannot, seller=seller)
        SellerRequestPageEvent.objects.create(
            access=cannot_access,
            request=cannot,
            seller=seller,
            event_type='cannot_fulfill',
        )

        url = reverse('control_panel:seller_detail', args=[seller.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertContains(response, 'Открыл заявку')
        self.assertContains(response, 'Перешёл в WhatsApp')
        self.assertContains(response, 'Позвонил')
        self.assertContains(response, 'Нет в наличии')
        self.assertContains(response, 'Не могу выполнить')
        self.assertContains(response, 'Ответа нет')
        self.assertContains(response, 'да')
        self.assertContains(response, 'нет')
        self.assertContains(
            response,
            f'href="/control/requests/parts/{opened.pk}/"',
            html=False,
        )
        self.assertNotIn(opened_access.token, body)
        self.assertNotIn(stock_access.token, body)
        self.assertNotIn(f'#{opened_access.pk}', body)
        self.assertNotIn(str(opened.access_token), body)
        self.assertNotIn('ServiceMatch', body)
        self.assertNotIn('Consent', body)
        self.assertNotIn('granted', body)
        self.assertNotIn('seller_req_page', body)
        self.assertNotRegex(body, r'\bsent\b')
        self.assertContains(response, 'Открыть в технической Django Admin')
        rows = {row['request_id']: row for row in response.context['requests']}
        self.assertTrue(rows[opened.pk]['opened'])
        self.assertTrue(rows[opened.pk]['whatsapp'])
        self.assertTrue(rows[opened.pk]['called'])
        self.assertEqual(rows[opened.pk]['result'], 'Ответа нет')
        self.assertEqual(rows[stock.pk]['result'], 'Нет в наличии')
        self.assertEqual(rows[cannot.pk]['result'], 'Не могу выполнить')
        self.assertEqual(rows[silent.pk]['result'], 'Ответа нет')
        self.assertFalse(rows[silent.pk]['opened'])
        self.assertEqual(response.context['out_of_stock'], 1)
        self.assertEqual(response.context['cannot_fulfill'], 1)

    def test_sto_and_service_request_filters_truncation_and_sort(self):
        self._login_staff()
        names = [
            'Полная компьютерная диагностика ходовой части автомобиля',
            'Замена масла двигателя с промывкой системы',
            'Ремонт автоматической коробки передач',
            'Покраска кузова с подбором цвета эмали',
        ]
        services = [Service.objects.create(name=name) for name in names]
        ordered_names = sorted(names)
        preview = summarize_names(ordered_names)
        hidden_service = ordered_names[3]
        sto = ServiceSeller.objects.create(
            name='Long Services STO',
            whatsapp='77016661111',
            password='unused',
            city='Алматы',
            district='Бостандыкский',
            seller_type='sto',
            is_active=True,
        )
        sto.services.add(*services)
        detailing = ServiceSeller.objects.create(
            name='Detail Atelier',
            whatsapp='77016661112',
            password='unused',
            city='Астана',
            district='Есильский',
            seller_type='detailing',
            is_active=False,
        )
        detailing.services.add(services[3])
        req = ServiceRequest.objects.create(
            service_type='sto',
            brand='Hyundai',
            model='Tucson',
            city='Алматы',
            phone='77017771111',
        )
        req.services.add(*services)
        other = ServiceRequest.objects.create(
            service_type='detailing',
            brand='BMW',
            model='X5',
            city='Астана',
            phone='77017771112',
        )
        other.services.add(services[3])
        ServiceMatch.objects.create(request=req, seller=sto, status='sent')
        ServiceWhatsAppMessageLog.objects.create(
            seller=sto,
            request=req,
            phone='77016661111',
            message_type='seller_request',
            status='sent',
        )

        self.assertEqual(preview, f'{ordered_names[0]}, {ordered_names[1]}, {ordered_names[2]} ещё 1')
        self.assertEqual(hidden_service, ordered_names[3])
        self.assertEqual(normalize_sto_sort(';drop table sellers'), 'name')
        self.assertEqual(normalize_sto_sort('city'), 'city')
        self.assertEqual(normalize_sto_sort('activity'), 'activity')

        sto_list = self.client.get('/control/partners/services/')
        self.assertEqual(sto_list.status_code, 200)
        sto_list_body = sto_list.content.decode('utf-8')
        self.assertContains(sto_list, preview)
        self.assertContains(sto_list, 'Detail Atelier')
        self.assertIn('cp-table-wrap', sto_list_body)
        self.assertIn('cp-cell-clip', sto_list_body)
        district = self.client.get('/control/partners/services/?district=Бостандыкский')
        self.assertContains(district, 'Long Services STO')
        self.assertContains(district, preview)
        self.assertNotContains(district, 'Detail Atelier')
        self.assertEqual(district.context['rows'][0].services, preview)
        self.assertNotIn(hidden_service, district.context['rows'][0].services)
        by_type = self.client.get('/control/partners/services/?seller_type=detailing')
        self.assertContains(by_type, 'Detail Atelier')
        self.assertNotContains(by_type, 'Long Services STO')
        by_service = self.client.get(f'/control/partners/services/?service={services[0].pk}')
        self.assertContains(by_service, 'Long Services STO')
        self.assertNotContains(by_service, 'Detail Atelier')
        inactive = self.client.get('/control/partners/services/?active=no')
        self.assertContains(inactive, 'Detail Atelier')
        self.assertNotContains(inactive, 'Long Services STO')
        city_sort = self.client.get('/control/partners/services/?sort=city')
        self.assertEqual(city_sort.status_code, 200)
        self.assertEqual(city_sort.context['filters']['sort'], 'city')
        activity_sort = self.client.get('/control/partners/services/?sort=activity')
        self.assertEqual(activity_sort.status_code, 200)
        self.assertEqual(activity_sort.context['filters']['sort'], 'activity')
        junk_sort = self.client.get('/control/partners/services/?sort=;drop table')
        self.assertEqual(junk_sort.status_code, 200)
        self.assertEqual(junk_sort.context['filters']['sort'], 'name')

        sto_detail = self.client.get(reverse('control_panel:sto_detail', args=[sto.pk]))
        for name in names:
            self.assertContains(sto_detail, name)
        sto_detail_body = sto_detail.content.decode('utf-8')
        self.assertNotIn('77016661111', sto_detail_body)
        self.assertNotIn('unused', sto_detail_body)
        self.assertNotIn('password', sto_detail_body)
        self.assertNotIn('credentials', sto_detail_body)
        self.assertNotIn(META_ERROR_SECRET, sto_detail_body)

        listing = self.client.get('/control/requests/services/')
        listing_body = listing.content.decode('utf-8')
        self.assertContains(listing, preview)
        rows = {row.pk: row for row in listing.context['rows']}
        self.assertEqual(rows[req.pk].services, preview)
        self.assertNotIn(hidden_service, rows[req.pk].services)
        self.assertEqual(rows[other.pk].services, names[3])
        self.assertContains(
            listing,
            f'href="/control/requests/services/{req.pk}/"',
            html=False,
        )
        self.assertContains(listing, 'Статусы и ответы СТО пока не собираются')
        self.assertNotIn('77017771111', listing_body)
        self.assertNotRegex(listing_body, r'\bsent\b')
        self.assertNotIn('ServiceMatch', listing_body)
        filtered = self.client.get(f'/control/requests/services/?service={services[0].pk}')
        self.assertContains(filtered, 'Hyundai')
        self.assertNotContains(filtered, 'BMW')

        detail = self.client.get(
            reverse('control_panel:service_request_detail', args=[req.pk])
        )
        for name in names:
            self.assertContains(detail, name)
        detail_body = detail.content.decode('utf-8')
        self.assertContains(detail, '7701•••11')
        self.assertNotIn('77017771111', detail_body)
        self.assertNotIn('77016661111', detail_body)
        self.assertNotIn('seller_request', detail_body)
        self.assertNotIn('ServiceMatch', detail_body)
        self.assertNotIn('Consent', detail_body)
        self.assertNotRegex(detail_body, r'\bsent\b')
        self.assertContains(detail, 'Отправлено')
        self.assertContains(detail, 'История WhatsApp-уведомлений')

    def test_horizontal_scroll_is_localized_to_table_wrap(self):
        css = (
            Path(__file__).resolve().parent / 'static' / 'control_panel' / 'control.css'
        ).read_text(encoding='utf-8')
        wrap_start = css.index('.cp-table-wrap')
        wrap_block = css[wrap_start : css.index('}', wrap_start) + 1]
        self.assertIn('overflow-x: auto', wrap_block)
        self.assertIn('overflow-x: hidden', css)
        self.assertIn('html.cp-html', css)
        self.assertIn('body.cp-body', css)
        self.assertIn('.cp-header', css)
        self.assertIn('.cp-sidebar', css)

    def _query_count(self, url):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return len(ctx), response

    def test_query_counts_do_not_grow_with_row_count(self):
        self._login_staff()
        seller = _seller(name='NPlus Seller', whatsapp='77015552100')
        now = timezone.now()

        def add_seller_request(index):
            req = _request(
                brand=f'NPlus{index:02d}',
                phone=f'77018{index:06d}'[:11],
            )
            Match.objects.create(
                request=req, seller=seller, status='sent', sent_at=now
            )
            RequestDispatch.objects.create(
                request=req,
                seller=seller,
                wave_number=1,
                position_number=1,
                status=RequestDispatch.STATUS_SENT,
                scheduled_at=now,
                sent_at=now,
            )
            access = create_seller_request_access(request=req, seller=seller)
            for event_type in ('page_open', 'whatsapp_click', 'call_click'):
                SellerRequestPageEvent.objects.create(
                    access=access,
                    request=req,
                    seller=seller,
                    event_type=event_type,
                )
            SellerRequestPageEvent.objects.create(
                access=access,
                request=req,
                seller=seller,
                event_type='out_of_stock' if index % 2 == 0 else 'cannot_fulfill',
            )
            return req

        add_seller_request(0)
        seller_url = reverse('control_panel:seller_detail', args=[seller.pk])
        one_seller_queries, one_seller_response = self._query_count(seller_url)
        self.assertEqual(len(one_seller_response.context['requests']), 1)
        for index in range(1, 30):
            add_seller_request(index)
        thirty_seller_queries, thirty_seller_response = self._query_count(seller_url)
        self.assertEqual(len(thirty_seller_response.context['requests']), 30)
        row = thirty_seller_response.context['requests'][0]
        self.assertIn('opened', row)
        self.assertIn('whatsapp', row)
        self.assertIn('called', row)
        self.assertIn('result', row)
        print(
            f'CONTROL_NPLUS seller_detail_1={one_seller_queries} '
            f'seller_detail_30={thirty_seller_queries}'
        )
        self.assertLessEqual(
            thirty_seller_queries,
            one_seller_queries + 2,
            f'seller detail N+1: 1 row={one_seller_queries}, '
            f'30 rows={thirty_seller_queries}',
        )

        sellers_one = self._query_count('/control/partners/sellers/')[0]
        for index in range(1, 50):
            _seller(name=f'List Seller {index}', whatsapp=f'77014{index:06d}'[:11])
        sellers_fifty = self._query_count('/control/partners/sellers/')[0]
        print(f'CONTROL_NPLUS seller_list_1={sellers_one} seller_list_50={sellers_fifty}')
        self.assertLessEqual(
            sellers_fifty,
            sellers_one + 3,
            f'seller list N+1: 1={sellers_one}, 50={sellers_fifty}',
        )

        service_names = [
            Service.objects.create(name=f'NPlus service {index}') for index in range(3)
        ]
        first_sto = ServiceSeller.objects.create(
            name='NPlus STO 0',
            whatsapp='77016652100',
            password='unused',
            city='Алматы',
            seller_type='sto',
        )
        first_sto.services.add(*service_names)
        sto_one = self._query_count('/control/partners/services/')[0]
        for index in range(1, 50):
            sto = ServiceSeller.objects.create(
                name=f'NPlus STO {index}',
                whatsapp=f'77016{index:06d}'[:11],
                password='unused',
                city='Алматы',
                seller_type='sto',
            )
            sto.services.add(*service_names)
        sto_fifty = self._query_count('/control/partners/services/')[0]
        print(f'CONTROL_NPLUS sto_list_1={sto_one} sto_list_50={sto_fifty}')
        self.assertLessEqual(
            sto_fifty,
            sto_one + 3,
            f'sto list N+1: 1={sto_one}, 50={sto_fifty}',
        )

        first_req = ServiceRequest.objects.create(
            service_type='sto',
            city='Алматы',
            phone='77017772100',
            brand='NPlusCar',
            model='One',
        )
        first_req.services.add(*service_names)
        ServiceMatch.objects.create(request=first_req, seller=first_sto, status='sent')
        ServiceWhatsAppMessageLog.objects.create(
            seller=first_sto,
            request=first_req,
            phone='77016652100',
            message_type='seller_request',
            status='sent',
        )
        services_one = self._query_count('/control/requests/services/')[0]
        for index in range(1, 50):
            sreq = ServiceRequest.objects.create(
                service_type='sto',
                city='Алматы',
                phone=f'77017{index:06d}'[:11],
                brand=f'STOBrand{index}',
                model='X',
            )
            sreq.services.add(*service_names)
            ServiceMatch.objects.create(request=sreq, seller=first_sto, status='sent')
        services_fifty = self._query_count('/control/requests/services/')[0]
        print(
            f'CONTROL_NPLUS service_list_1={services_one} '
            f'service_list_50={services_fifty}'
        )
        self.assertLessEqual(
            services_fifty,
            services_one + 3,
            f'service request list N+1: 1={services_one}, 50={services_fifty}',
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
