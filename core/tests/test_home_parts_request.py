"""Homepage short-form request: create, dispatch-all, idempotency, validation."""
from __future__ import annotations

from tempfile import TemporaryDirectory
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from catalog.models import Brand as CatalogBrand
from catalog.models import CarModel as CatalogCarModel
from catalog.models import Country as CatalogCountry
from catalog.models import Product
from catalog.models import SellerProfile
from core.models import (
    Brand as CoreBrand,
    BroadcastSettings,
    CarModel as CoreCarModel,
    Country as CoreCountry,
    Request,
    RequestDispatch,
    RequestPhoto,
    Seller,
)
from core.request_dispatch_service import process_due_dispatch_waves
from core.tests.test_request_dispatch_waves import _ensure_broadcast_settings
from django.contrib.auth.models import User


def _png() -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new('RGB', (40, 40), color=(12, 80, 160)).save(buffer, format='PNG')
    return SimpleUploadedFile('part.png', buffer.getvalue(), content_type='image/png')


def _core_vehicle(brand_name='Toyota', model_name='Camry', transport_type='car', country='Япония'):
    country_obj, _ = CoreCountry.objects.get_or_create(name=country)
    brand, _ = CoreBrand.objects.get_or_create(
        country=country_obj,
        name=brand_name,
        transport_type=transport_type,
    )
    model, _ = CoreCarModel.objects.get_or_create(
        brand=brand,
        name=model_name,
        transport_type=transport_type,
    )
    return brand, model


def _catalog_vehicle(brand_name='Toyota', model_name='Camry', country='Япония'):
    country_obj, _ = CatalogCountry.objects.get_or_create(name=country)
    brand, _ = CatalogBrand.objects.get_or_create(country=country_obj, name=brand_name)
    model, _ = CatalogCarModel.objects.get_or_create(brand=brand, name=model_name)
    return brand, model


def _seller(**kwargs):
    defaults = {
        'name': 'Almaty car seller',
        'whatsapp': '77010000001',
        'transport_type': 'car',
        'city': 'Алматы',
        'is_active': True,
        'is_paused': False,
        'receive_requests': True,
        'is_test_seller': False,
        'brand': 'Hyundai',
        'model': 'Solaris',
    }
    defaults.update(kwargs)
    return Seller.objects.create(**defaults)


@override_settings(
    PUBLIC_BASE_URL='https://zpt.kz',
    HOME_PARTS_MAX_PER_HOUR=0,
    HOME_PARTS_MAX_PER_PHONE_HOUR=0,
)
class HomePartsRequestTests(TestCase):
    def setUp(self):
        self.client = Client()
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        self.core_brand, self.core_model = _core_vehicle()
        seed_country, _ = CatalogCountry.objects.get_or_create(name='Корея')
        CatalogBrand.objects.get_or_create(country=seed_country, name='SeedBrandForIdGap')
        self.catalog_brand, self.catalog_model = _catalog_vehicle()
        self.user = User.objects.create_user('home-parts-seller', password='pass')
        self.profile = SellerProfile.objects.create(
            user=self.user,
            name='Home Seller',
            phone='77001112233',
            city='Астана',
        )
        self.live_car = _seller(name='Car Almaty', whatsapp='77010000001', city='Алматы')
        self.live_truck = _seller(
            name='Truck Astana',
            whatsapp='77010000002',
            city='Астана',
            transport_type='truck',
            brand='KAMAZ',
            model='65115',
        )
        self.paused = _seller(
            name='Paused',
            whatsapp='77010000003',
            is_paused=True,
            receive_requests=True,
        )
        self.inactive = _seller(
            name='Inactive',
            whatsapp='77010000004',
            is_active=False,
        )
        self.no_receive = _seller(
            name='Opted out',
            whatsapp='77010000005',
            receive_requests=False,
        )
        self.test_only = _seller(
            name='Test only',
            whatsapp='77010000006',
            is_test_seller=True,
            receive_requests=False,
        )

    def _post(self, **extra):
        data = {
            'query': 'тормозные колодки',
            'brand': 'Toyota',
            'model': 'Camry',
            'brand_id': str(self.core_brand.id),
            'model_id': str(self.core_model.id),
            'city': 'Алматы',
            'phone': '87015556677',
            'consent': '1',
            'idempotency_key': extra.pop('idempotency_key', 'key-home-1'),
        }
        data.update(extra)
        with patch('core.views._send_buyer_whatsapp_notification_async') as buyer_wa, patch(
            'core.views.schedule_instagram_publication_for_request',
        ) as instagram:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post('/api/home-parts-request/', data=data)
        return response, buyer_wa, instagram

    def test_successful_short_form_creates_one_request_and_queues_all(self):
        response, buyer_wa, instagram = self._post()
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertTrue(payload['queued'])
        self.assertEqual(payload['message'], 'Запрос принят. Ожидайте предложения в WhatsApp.')
        self.assertEqual(Request.objects.count(), 1)
        req = Request.objects.get()
        self.assertEqual(req.source, Request.SOURCE_HOME_SHORT)
        self.assertEqual(req.dispatch_mode, Request.DISPATCH_MODE_ALL_KZ)
        self.assertEqual(req.search_scope, 'kazakhstan')
        self.assertEqual(req.phone, '77015556677')
        self.assertEqual(req.brand, 'Toyota')
        self.assertEqual(req.model, 'Camry')
        self.assertEqual(req.transport_type, 'car')
        queued_ids = set(
            RequestDispatch.objects.filter(request=req).values_list('seller_id', flat=True)
        )
        self.assertEqual(queued_ids, {self.live_car.id, self.live_truck.id})
        buyer_wa.assert_called_once()
        instagram.assert_called_once()

    def test_article_without_vehicle(self):
        response, _, _ = self._post(
            query='52119-0K040',
            brand='',
            model='',
            brand_id='',
            model_id='',
            idempotency_key='key-article',
        )
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get()
        self.assertEqual(req.article, '52119-0K040')
        self.assertEqual(req.brand, '')
        self.assertEqual(req.model, '')
        self.assertEqual(req.transport_type, '')

    def test_name_without_vehicle_rejected(self):
        response, _, _ = self._post(
            query='фильтр H75',
            brand='',
            model='',
            brand_id='',
            model_id='',
            idempotency_key='key-name',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Request.objects.count(), 0)
        self.assertIn('марку', response.json()['error'].lower())

    def test_missing_required_fields(self):
        response, _, _ = self._post(
            query='',
            phone='',
            city='',
            consent='',
            idempotency_key='key-missing',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Request.objects.count(), 0)

    def test_saves_year_vin_and_photo(self):
        response, _, _ = self._post(
            year='2018',
            vin='JTDBR32E720012345',
            photos=_png(),
            idempotency_key='key-extra',
        )
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get()
        self.assertEqual(req.year, 2018)
        self.assertEqual(req.vin, 'JTDBR32E720012345')
        self.assertEqual(req.photos.count(), 1)

    def test_multiple_positions_one_request_separate_search(self):
        Product.objects.create(
            title='колодки передние Camry',
            slug='pads-camry',
            article='PAD-1',
            price=1000,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=self.catalog_brand,
            car_model=self.catalog_model,
        )
        Product.objects.create(
            title='масляный фильтр Camry',
            slug='oil-camry',
            article='OIL-1',
            price=2000,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=self.catalog_brand,
            car_model=self.catalog_model,
        )
        response, _, _ = self._post(
            query='колодки, масляный фильтр',
            idempotency_key='key-multi',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Request.objects.count(), 1)
        groups = response.json()['products']
        self.assertEqual([item['query'] for item in groups], ['колодки', 'масляный фильтр'])
        self.assertGreaterEqual(len(groups[0]['products']), 1)
        self.assertGreaterEqual(len(groups[1]['products']), 1)

    def test_core_and_catalog_ids_are_not_assumed_equal(self):
        self.assertNotEqual(self.core_brand.id, self.catalog_brand.id)
        Product.objects.create(
            title='Колодки по каталогу',
            slug='pads-id-mismatch',
            article='PAD-ID',
            price=1500,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=self.catalog_brand,
            car_model=self.catalog_model,
        )
        response, _, _ = self._post(
            query='Колодки по каталогу',
            brand_id=str(self.core_brand.id),
            model_id=str(self.core_model.id),
            idempotency_key='key-ids',
        )
        self.assertEqual(response.status_code, 200, response.content)
        titles = [
            item['title']
            for group in response.json()['products']
            for item in group['products']
        ]
        self.assertIn('Колодки по каталогу', titles)

    def test_manual_model_does_not_create_catalog_rows(self):
        catalog_models_before = CatalogCarModel.objects.count()
        core_models_before = CoreCarModel.objects.count()
        response, _, _ = self._post(
            query='ремень ГРМ',
            brand='Toyota',
            model='CustomWagon',
            brand_id=str(self.core_brand.id),
            model_id='',
            idempotency_key='key-manual',
        )
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get()
        self.assertEqual(req.model, 'CustomWagon')
        self.assertEqual(CatalogCarModel.objects.count(), catalog_models_before)
        self.assertEqual(CoreCarModel.objects.count(), core_models_before)

    def test_off_mode_saves_without_queue_or_promise(self):
        _ensure_broadcast_settings(mode=BroadcastSettings.MODE_OFF, emergency_stop=False)
        response, _, _ = self._post(idempotency_key='key-off')
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertFalse(payload['queued'])
        self.assertIn('сохранена', payload['message'])
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(RequestDispatch.objects.count(), 0)

    def test_test_mode_only_test_sellers(self):
        _ensure_broadcast_settings(mode=BroadcastSettings.MODE_TEST, emergency_stop=False)
        response, _, _ = self._post(idempotency_key='key-test')
        self.assertEqual(response.status_code, 200, response.content)
        seller_ids = set(RequestDispatch.objects.values_list('seller_id', flat=True))
        self.assertEqual(seller_ids, {self.test_only.id})

    def test_emergency_stop_does_not_queue(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            emergency_stop=True,
        )
        response, _, _ = self._post(idempotency_key='key-stop')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()['queued'])
        self.assertEqual(RequestDispatch.objects.count(), 0)

    def test_disabled_after_queue_is_not_sent_and_next_wave_continues(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        response, _, _ = self._post(idempotency_key='key-skip')
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get()
        car_dispatch = RequestDispatch.objects.get(request=req, seller=self.live_car)
        self.live_car.receive_requests = False
        self.live_car.save(update_fields=['receive_requests'])
        with patch('core.views.send_whatsapp_template', return_value={'ok': True, 'message_id': 'wamid.1'}):
            stats = process_due_dispatch_waves()
        car_dispatch.refresh_from_db()
        self.assertEqual(car_dispatch.status, RequestDispatch.STATUS_PAUSED)
        self.assertEqual(stats['sent'], 1)
        truck_dispatch = RequestDispatch.objects.get(request=req, seller=self.live_truck)
        self.assertEqual(truck_dispatch.status, RequestDispatch.STATUS_SENT)

    def test_repeat_post_does_not_duplicate(self):
        first, buyer_wa, instagram = self._post(idempotency_key='same-key')
        second, buyer_wa2, instagram2 = self._post(idempotency_key='same-key')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(RequestDispatch.objects.count(), 2)
        self.assertTrue(second.json()['replay'])
        self.assertNotIn('request_page_url', second.json())
        self.assertEqual(buyer_wa.call_count, 1)
        self.assertEqual(buyer_wa2.call_count, 0)
        self.assertEqual(instagram.call_count, 1)
        self.assertEqual(instagram2.call_count, 0)

    def test_repeat_key_with_changed_body_is_rejected(self):
        first, _, _ = self._post(idempotency_key='same-key')
        second, buyer_wa, instagram = self._post(
            idempotency_key='same-key',
            query='масляный фильтр',
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        payload = second.json()
        self.assertTrue(payload.get('can_retry_as_new'))
        self.assertNotIn('result_url', payload)
        self.assertNotIn('id', payload)
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(Request.objects.get().description, 'тормозные колодки')
        buyer_wa.assert_not_called()
        instagram.assert_not_called()

    def test_foreign_key_does_not_reveal_request(self):
        self._post(idempotency_key='owner-key')
        response, _, _ = self._post(
            idempotency_key='owner-key',
            phone='87019990000',
        )
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertNotIn('result_url', payload)
        self.assertNotIn('access_token', str(payload))
        self.assertEqual(Request.objects.count(), 1)

    def test_empty_idempotency_key_is_rejected(self):
        response, _, _ = self._post(idempotency_key='')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Request.objects.count(), 0)

    def test_duplicate_whatsapp_is_queued_once(self):
        _seller(
            name='Eight prefix shop',
            whatsapp='87010000001',
            city='Шымкент',
            brand='Kia',
            model='Rio',
        )
        _seller(
            name='Plus prefix shop',
            whatsapp='+77010000001',
            city='Караганда',
            brand='Kia',
            model='Rio',
        )
        _seller(
            name='Invalid number shop',
            whatsapp='00000000000',
            city='Актобе',
        )
        response, _, _ = self._post(idempotency_key='key-dup-wa')
        self.assertEqual(response.status_code, 200, response.content)
        from core.phone_utils import normalize_phone_for_whatsapp
        phones = [
            normalize_phone_for_whatsapp(item.seller.whatsapp)
            for item in RequestDispatch.objects.select_related('seller')
        ]
        self.assertEqual(phones.count('77010000001'), 1)
        self.assertEqual(set(phones), {'77010000001', '77010000002'})
        self.assertNotIn(None, phones)

    def test_reenabled_seller_does_not_unpause_this_request(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        response, _, _ = self._post(idempotency_key='key-reenable')
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get()
        car_dispatch = RequestDispatch.objects.get(request=req, seller=self.live_car)
        self.live_car.receive_requests = False
        self.live_car.save(update_fields=['receive_requests'])
        with patch('core.views.send_whatsapp_template', return_value={'ok': True, 'message_id': 'wamid.1'}):
            process_due_dispatch_waves()
        car_dispatch.refresh_from_db()
        self.assertEqual(car_dispatch.status, RequestDispatch.STATUS_PAUSED)
        self.live_car.receive_requests = True
        self.live_car.save(update_fields=['receive_requests'])
        with patch('core.views.send_whatsapp_template', return_value={'ok': True, 'message_id': 'wamid.2'}):
            process_due_dispatch_waves()
        car_dispatch.refresh_from_db()
        self.assertEqual(car_dispatch.status, RequestDispatch.STATUS_PAUSED)

    def test_unknown_catalog_brand_does_not_show_other_car(self):
        other_brand, other_model = _catalog_vehicle('Hyundai', 'Solaris')
        Product.objects.create(
            title='колодки передние Solaris',
            slug='pads-other-car',
            article='HYU-PAD',
            price=3000,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=other_brand,
            car_model=other_model,
        )
        response, _, _ = self._post(
            query='колодки',
            brand='NoSuchBrandXYZ',
            model='CustomUnknown',
            brand_id='',
            model_id='',
            idempotency_key='key-unknown-brand',
        )
        self.assertEqual(response.status_code, 200, response.content)
        titles = [
            item['title']
            for group in response.json()['products']
            for item in group['products']
        ]
        self.assertNotIn('колодки передние Solaris', titles)

    @override_settings(HOME_PARTS_MAX_PER_HOUR=0, HOME_PARTS_MAX_PER_PHONE_HOUR=1)
    def test_phone_limit_uses_normalized_number_and_allows_replay(self):
        first, _, _ = self._post(
            phone='87015556677',
            idempotency_key='limit-key',
        )
        replay, _, _ = self._post(
            phone='77015556677',
            idempotency_key='limit-key',
        )
        blocked, _, _ = self._post(
            phone='8 (701) 555-66-77',
            idempotency_key='limit-key-new',
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.json()['replay'])
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(Request.objects.count(), 1)

    @override_settings(
        HOME_PARTS_MAX_PER_HOUR=1,
        HOME_PARTS_MAX_PER_PHONE_HOUR=0,
        HOME_PARTS_NUM_PROXIES=1,
    )
    def test_spoofed_forwarded_for_cannot_bypass_proxy_ip(self):
        data = {
            'query': 'тормозные колодки',
            'brand': 'Toyota',
            'model': 'Camry',
            'brand_id': str(self.core_brand.id),
            'model_id': str(self.core_model.id),
            'city': 'Алматы',
            'phone': '87015556677',
            'consent': '1',
        }
        with patch('core.views._send_buyer_whatsapp_notification_async'), patch(
            'core.views.schedule_instagram_publication_for_request',
        ):
            with self.captureOnCommitCallbacks(execute=True):
                first = self.client.post(
                    '/api/home-parts-request/',
                    data={**data, 'idempotency_key': 'ip-key-1'},
                    HTTP_X_FORWARDED_FOR='203.0.113.10',
                    REMOTE_ADDR='10.0.0.1',
                )
                spoofed = self.client.post(
                    '/api/home-parts-request/',
                    data={**data, 'idempotency_key': 'ip-key-2'},
                    HTTP_X_FORWARDED_FOR='198.51.100.20, 203.0.113.10',
                    REMOTE_ADDR='10.0.0.1',
                )
                other_client = self.client.post(
                    '/api/home-parts-request/',
                    data={
                        **data,
                        'phone': '87015550000',
                        'idempotency_key': 'ip-key-3',
                    },
                    HTTP_X_FORWARDED_FOR='198.51.100.20',
                    REMOTE_ADDR='10.0.0.1',
                )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(spoofed.status_code, 429)
        self.assertEqual(other_client.status_code, 200, other_client.content)
        self.assertEqual(Request.objects.count(), 2)

    def test_search_failure_does_not_cancel_request(self):
        with patch(
            'catalog.home_parts_search.search_home_parts',
            side_effect=RuntimeError('catalog down'),
        ):
            response, _, _ = self._post(idempotency_key='key-search-fail')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Request.objects.count(), 1)
        self.assertTrue(response.json()['search_failed'])
        self.assertTrue(response.json()['queued'])

    def test_classic_create_request_still_works(self):
        with patch('core.views._send_buyer_whatsapp_notification_async'), patch(
            'core.views._find_matching_sellers',
            return_value=([self.live_car], 'matched'),
        ):
            response = self.client.post(
                '/api/create-request/',
                data={
                    'transport_type': 'car',
                    'brand': 'Toyota',
                    'model': 'Camry',
                    'category': 'Тормоза',
                    'city': 'Алматы',
                    'phone': '77001112233',
                    'search_scope': 'city',
                },
            )
        self.assertEqual(response.status_code, 200)
        req = Request.objects.get()
        self.assertEqual(req.source, Request.SOURCE_CLASSIC)
        self.assertEqual(req.dispatch_mode, Request.DISPATCH_MODE_MATCHED)

    def test_legacy_catalog_query_still_lists_products(self):
        Product.objects.create(
            title='Фара Camry',
            slug='headlight-legacy',
            article='LMP-1',
            price=5000,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
        )
        response = self.client.get('/', {'q': 'Фара Camry'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Фара Camry')
        self.assertContains(response, 'Запчасти в наличии')

    def test_request_parts_page_still_works(self):
        response = self.client.get('/request-parts/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'request-parts-form-v5.js')
        self.assertContains(response, 'Отправить заявку')

    def test_seller_register_page_still_works(self):
        response = self.client.get('/seller/register/')
        self.assertEqual(response.status_code, 200)

    def test_article_mismatch_does_not_claim_compatibility(self):
        other_brand, other_model = _catalog_vehicle('Hyundai', 'Solaris')
        Product.objects.create(
            title='Фара Hyundai',
            slug='lamp-hyundai',
            article='52119-0K040',
            price=9000,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=other_brand,
            car_model=other_model,
        )
        response, _, _ = self._post(
            query='52119-0K040',
            idempotency_key='key-compat',
        )
        self.assertEqual(response.status_code, 200, response.content)
        hits = response.json()['products'][0]['products']
        self.assertEqual(hits[0]['article'], '52119-0K040')
        self.assertEqual(hits[0]['compatibility'], 'unconfirmed')
        self.assertEqual(hits[0]['match_kind'], 'article_exact')

    def test_normalized_article_matches_hyphen_space_and_case(self):
        Product.objects.create(
            title='Фара точная',
            slug='lamp-exact-hyphen',
            article='52119-0K040',
            price=9000,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=self.catalog_brand,
            car_model=self.catalog_model,
        )
        for index, query in enumerate(('521190K040', '52119 0k040', '52119-0k040')):
            with self.subTest(query=query):
                response, _, _ = self._post(
                    query=query,
                    idempotency_key=f'key-art-norm-{index}',
                )
                self.assertEqual(response.status_code, 200, response.content)
                hits = response.json()['products'][0]['products']
                self.assertEqual(hits[0]['article'], '52119-0K040')
                self.assertEqual(hits[0]['match_kind'], 'article_exact')

    def test_exact_article_is_not_dropped_by_candidate_window(self):
        for index in range(90):
            Product.objects.create(
                title=f'Похожий артикул {index}',
                slug=f'near-article-{index}',
                article=f'52119-NEAR-{index:03d}',
                price=100 + index,
                seller_name=self.profile.name,
                whatsapp_number=self.profile.phone,
                status='active',
            )
        Product.objects.create(
            title='Нужный артикул',
            slug='needed-article',
            article='52119-0K040',
            price=5000,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
        )
        response, _, _ = self._post(
            query='521190K040',
            idempotency_key='key-art-window',
        )
        self.assertEqual(response.status_code, 200, response.content)
        articles = [
            item['article']
            for group in response.json()['products']
            for item in group['products']
        ]
        self.assertIn('52119-0K040', articles)

    def test_missing_model_does_not_search_whole_brand_by_name(self):
        Product.objects.create(
            title='колодки передние Camry',
            slug='pads-camry-name',
            article='TOY-PAD',
            price=1500,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=self.catalog_brand,
            car_model=self.catalog_model,
        )
        response, _, _ = self._post(
            query='колодки',
            brand='Toyota',
            model='CustomMissingModel',
            brand_id=str(self.core_brand.id),
            model_id='',
            idempotency_key='key-missing-model',
        )
        self.assertEqual(response.status_code, 200, response.content)
        titles = [
            item['title']
            for group in response.json()['products']
            for item in group['products']
        ]
        self.assertNotIn('колодки передние Camry', titles)

    def test_model_from_other_brand_is_not_used(self):
        other_brand, other_model = _catalog_vehicle('Hyundai', 'Camry')
        Product.objects.create(
            title='колодки Hyundai Camry',
            slug='pads-hyu-camry',
            article='HYU-CAM',
            price=1400,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=other_brand,
            car_model=other_model,
        )
        response, _, _ = self._post(
            query='колодки',
            brand='NoSuchBrandXYZ',
            model='Camry',
            brand_id='',
            model_id='',
            idempotency_key='key-other-brand-model',
        )
        self.assertEqual(response.status_code, 200, response.content)
        titles = [
            item['title']
            for group in response.json()['products']
            for item in group['products']
        ]
        self.assertNotIn('колодки Hyundai Camry', titles)

    def test_country_mismatch_does_not_silently_drop_country(self):
        from catalog.home_parts_search import search_home_parts

        Product.objects.create(
            title='колодки передние Camry',
            slug='pads-country-mismatch',
            article='JP-PAD',
            price=1100,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=self.catalog_brand,
            car_model=self.catalog_model,
        )
        groups = search_home_parts(
            ['колодки'],
            brand_name='Toyota',
            model_name='Camry',
            country='Германия',
        )
        titles = [hit.product.title for group in groups for hit in group.hits]
        self.assertNotIn('колодки передние Camry', titles)

    def test_changed_photo_on_same_key_is_conflict(self):
        first, _, _ = self._post(photos=_png(), idempotency_key='key-photo-fp')
        other = BytesIO()
        Image.new('RGB', (40, 40), color=(200, 10, 10)).save(other, format='PNG')
        second, _, _ = self._post(
            photos=SimpleUploadedFile(
                'other.png',
                other.getvalue(),
                content_type='image/png',
            ),
            idempotency_key='key-photo-fp',
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 409)
        self.assertTrue(second.json().get('can_retry_as_new'))
        self.assertEqual(Request.objects.count(), 1)

    def test_confirmation_after_sent_is_not_unavailable(self):
        response, _, _ = self._post(idempotency_key='key-sent-status')
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get()
        RequestDispatch.objects.filter(request=req).update(
            status=RequestDispatch.STATUS_SENT,
        )
        replay, _, _ = self._post(idempotency_key='key-sent-status')
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(
            replay.json()['message'],
            'Запрос принят. Ожидайте предложения в WhatsApp.',
        )
        page = self.client.get('/', {'home_request': str(req.access_token)})
        html = page.content.decode()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Ожидайте предложения в WhatsApp')
        self.assertNotContains(page, 'отправить не можем')
        self.assertEqual(page['X-Robots-Tag'], 'noindex, nofollow')
        self.assertIn('private', page['Cache-Control'])
        self.assertEqual(page['Referrer-Policy'], 'no-referrer')
        self.assertContains(page, 'Новый запрос')
        self.assertRegex(html, r'id="home-parts-form"[\s\S]*?\bhidden\b')
        self.assertNotIn(str(req.access_token), html)
        self.assertNotContains(page, 'googletagmanager.com')
        RequestDispatch.objects.filter(request=req).update(
            status=RequestDispatch.STATUS_PAUSED,
        )
        paused_page = self.client.get('/', {'home_request': str(req.access_token)})
        self.assertContains(paused_page, 'заявка сохранена')
        self.assertContains(paused_page, 'отправить не можем')
        self.assertNotContains(paused_page, 'Ожидайте предложения в WhatsApp')

    def test_home_request_omits_gtm_when_configured(self):
        import os
        from unittest.mock import patch

        response, _, _ = self._post(idempotency_key='key-gtm-result')
        req = Request.objects.get()
        with patch.dict(os.environ, {'GOOGLE_TAG_MANAGER_ID': 'GTM-ABC123'}, clear=False):
            home = self.client.get('/')
            result = self.client.get('/', {'home_request': str(req.access_token)})
            invalid = self.client.get('/', {'home_request': 'abc'})
        self.assertContains(home, 'googletagmanager.com/gtm.js')
        self.assertContains(home, 'googletagmanager.com/ns.html')
        self.assertNotContains(result, 'googletagmanager.com')
        self.assertNotContains(invalid, 'googletagmanager.com')
        self.assertNotContains(result, 'dataLayer')

    def test_invalid_home_request_token_does_not_500(self):
        import uuid
        invalid = self.client.get('/', {'home_request': 'abc'})
        unknown = self.client.get('/', {'home_request': str(uuid.uuid4())})
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertNotContains(invalid, 'id="home-request-result"')
        self.assertNotContains(unknown, 'id="home-request-result"')
        self.assertEqual(invalid['X-Robots-Tag'], 'noindex, nofollow')
        self.assertEqual(unknown['X-Robots-Tag'], 'noindex, nofollow')

    def test_consent_is_not_marketing_opt_in(self):
        response, _, _ = self._post(idempotency_key='key-consent')
        self.assertEqual(response.status_code, 200)
        from core.models import (
            CONTACT_CONSENT_PURPOSE_MARKETING,
            CONTACT_CONSENT_STATUS_GRANTED,
            ContactConsent,
        )
        self.assertFalse(
            ContactConsent.objects.filter(
                purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
                status=CONTACT_CONSENT_STATUS_GRANTED,
            ).exists()
        )


class VehicleSuggestTests(TestCase):
    def setUp(self):
        _core_vehicle('Toyota', 'Camry')
        _core_vehicle('Toyota', 'Corolla')
        _core_vehicle('Mercedes-Benz', 'E-Class', country='Германия')
        _core_vehicle('Haval', 'Jolion', country='Китай')
        country, _ = CoreCountry.objects.get_or_create(name='Россия')
        CoreBrand.objects.get_or_create(
            country=country,
            name='KAMAZ',
            transport_type='truck',
        )

    def test_brand_prefix_and_cyrillic(self):
        response = self.client.get('/api/vehicle-suggest/', {'kind': 'brand', 'q': 'тойо'})
        names = [item['name'] for item in response.json()['items']]
        self.assertIn('Toyota', names)

    def test_models_limited_to_brand(self):
        brand = CoreBrand.objects.get(name='Toyota', transport_type='car')
        response = self.client.get(
            '/api/vehicle-suggest/',
            {'kind': 'model', 'q': 'cam', 'brand_id': brand.id},
        )
        names = [item['name'] for item in response.json()['items']]
        self.assertIn('Camry', names)
        self.assertNotIn('KAMAZ', names)

    def test_russian_alias_prefixes(self):
        from core.services.vehicle_suggest import _score_name, suggest_brands, suggest_models

        self.assertIsNotNone(_score_name('мерс', 'Mercedes-Benz'))
        self.assertIsNotNone(_score_name('джол', 'Jolion'))
        brands = [item['name'] for item in suggest_brands('мерс')]
        self.assertIn('Mercedes-Benz', brands)
        haval = CoreBrand.objects.get(name='Haval', transport_type='car')
        models = [
            item['name']
            for item in suggest_models('джол', brand_id=haval.id)
        ]
        self.assertIn('Jolion', models)


@override_settings(PUBLIC_BASE_URL='https://zpt.kz', ALLOWED_HOSTS=['*'])
class HomePartsSellerPageTests(TestCase):
    def test_year_vin_and_photo_shown_to_seller(self):
        from core.services.seller_request_access import create_seller_request_access

        media_tmp = TemporaryDirectory()
        self.addCleanup(media_tmp.cleanup)
        with self.settings(MEDIA_ROOT=media_tmp.name):
            req = Request.objects.create(
                transport_type='car',
                brand='Toyota',
                model='Camry',
                year=2018,
                vin='JTDBR32E720012345',
                city='Алматы',
                phone='77019990011',
                description='колодки',
                source=Request.SOURCE_HOME_SHORT,
                dispatch_mode=Request.DISPATCH_MODE_ALL_KZ,
            )
            photo = RequestPhoto.objects.create(request=req, image=_png())
            seller = _seller(whatsapp='77015550101')
            access = create_seller_request_access(request=req, seller=seller)
            client = Client(HTTP_HOST='zpt.kz')
            response = client.get(reverse('seller_request_link', kwargs={'token': access.token}))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '2018')
            self.assertContains(response, 'JTDBR32E720012345')
            self.assertContains(response, photo.image.url)


@override_settings(
    HOME_PARTS_MAX_PER_HOUR=1,
    HOME_PARTS_MAX_PER_PHONE_HOUR=0,
    HOME_PARTS_NUM_PROXIES=0,
)
class HomePartsRateLimitConcurrencyTests(TransactionTestCase):
    def test_concurrent_bucket_create_uses_savepoint(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'PostgreSQL недоступен: конкурентное создание счётчика '
                'через отдельные соединения не проверялось'
            )

        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from django.db import connections

        from core.models import HomePartsRateBucket
        from core.services.public_rate_limit import home_parts_rate_limit_allowed

        bucket_key = 'ip:203.0.113.80'
        factory = RequestFactory()
        barrier = Barrier(2, timeout=5)
        original_create = HomePartsRateBucket.objects.create

        def create_after_both_missed(*args, **kwargs):
            barrier.wait(timeout=5)
            return original_create(*args, **kwargs)

        def attempt():
            close_old_connections()
            try:
                request = factory.post('/', REMOTE_ADDR='203.0.113.80')
                return ('ok', home_parts_rate_limit_allowed(request))
            except Exception as exc:
                return ('err', exc)
            finally:
                connections.close_all()

        with patch.object(HomePartsRateBucket.objects, 'create', create_after_both_missed):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: attempt(), range(2)))

        errors = [item[1] for item in results if item[0] == 'err']
        allowed = [item[1] for item in results if item[0] == 'ok']
        self.assertEqual(errors, [])
        self.assertEqual(allowed.count(True), 1)
        self.assertEqual(allowed.count(False), 1)
        buckets = list(HomePartsRateBucket.objects.filter(key=bucket_key))
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0].hits, 1)


