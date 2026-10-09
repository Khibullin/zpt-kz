"""Homepage short-form request: matched dispatch, idempotency, validation."""
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
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_PURPOSE_SERVICE,
    CONTACT_CONSENT_SOURCE_REQUEST_FORM,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    ContactConsent,
    Country as CoreCountry,
    PartCategory,
    Request,
    RequestDispatch,
    RequestPhoto,
    Seller,
)
from core.services.home_parts_request import HOME_PARTS_CONSENT_TEXT_VERSION
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
        self.japan = self.core_brand.country
        PartCategory.objects.create(name='Тормоза')
        PartCategory.objects.create(name='Трансмиссия')
        PartCategory.objects.create(name='Кузов')
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
        self.live_car = _seller(
            name='Car Almaty',
            whatsapp='77010000001',
            city='Алматы',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            country_fk=self.japan,
        )
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
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            country_fk=self.japan,
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
            'transport_type': 'car',
            'category': 'Тормоза',
            'idempotency_key': extra.pop('idempotency_key', 'key-home-1'),
        }
        data.update(extra)
        with patch('core.views._send_buyer_whatsapp_notification_async') as buyer_wa, patch(
            'core.views.schedule_instagram_publication_for_request',
        ) as instagram:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post('/api/home-parts-request/', data=data)
        return response, buyer_wa, instagram

    def test_home_short_creates_matched_kazakhstan_request(self):
        response, buyer_wa, instagram = self._post()
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertTrue(payload['queued'])
        self.assertEqual(payload['matches'], 1)
        self.assertEqual(payload['message'], 'Запрос принят. Ожидайте предложения в WhatsApp.')
        self.assertEqual(Request.objects.count(), 1)
        req = Request.objects.get()
        self.assertEqual(req.source, Request.SOURCE_HOME_SHORT)
        self.assertEqual(req.dispatch_mode, Request.DISPATCH_MODE_MATCHED)
        self.assertEqual(req.search_scope, 'city')
        self.assertEqual(req.category, 'Тормоза')
        self.assertEqual(req.phone, '77015556677')
        self.assertEqual(req.brand, 'Toyota')
        self.assertEqual(req.model, 'Camry')
        self.assertEqual(req.transport_type, 'car')
        queued_ids = set(
            RequestDispatch.objects.filter(request=req).values_list('seller_id', flat=True)
        )
        self.assertEqual(queued_ids, {self.live_car.id})
        self.assertNotIn(self.live_truck.id, queued_ids)
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
        self.assertEqual(req.transport_type, 'car')
        self.assertEqual(req.category, 'Тормоза')
        self.assertEqual(req.dispatch_mode, Request.DISPATCH_MODE_MATCHED)
        queued_ids = set(req.dispatches.values_list('seller_id', flat=True))
        self.assertEqual(queued_ids, {self.live_car.id})

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
            idempotency_key='key-missing',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Request.objects.count(), 0)

    def test_city_is_canonicalized_and_unknown_rejected(self):
        from core.kazakhstan_locations import KAZAKHSTAN_CITIES as core_cities
        from orders.constants import KAZAKHSTAN_CITIES as order_cities

        self.assertIs(core_cities, order_cities)
        cases = (
            ('Алматы', 'key-city-exact'),
            (' алматы ', 'key-city-trim'),
            ('АЛМАТЫ', 'key-city-upper'),
        )
        for city, key in cases:
            response, _, _ = self._post(city=city, idempotency_key=key)
            self.assertEqual(response.status_code, 200, response.content)
            req = Request.objects.get(idempotency_key=key)
            self.assertEqual(req.city, 'Алматы')

        legacy, _, _ = self._post(city='Аматы', idempotency_key='key-city-typo')
        self.assertEqual(legacy.status_code, 200, legacy.content)
        self.assertEqual(
            Request.objects.get(idempotency_key='key-city-typo').city,
            'Алматы',
        )

        before = Request.objects.count()
        missing, _, _ = self._post(
            city='Город-которого-нет',
            idempotency_key='key-city-unknown',
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()['fields']['city'], 'Выберите город из списка.')
        self.assertEqual(Request.objects.count(), before)

        empty, _, _ = self._post(city='', idempotency_key='key-city-empty')
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()['fields']['city'], 'Укажите город.')
        self.assertEqual(Request.objects.count(), before)

    def test_city_exact_aliases_accepted_typos_rejected(self):
        from core.kazakhstan_locations import KAZAKHSTAN_CITIES as core_cities

        self.assertIn('Жезказган', core_cities)

        accepted = (
            ('Almaty', 'Алматы', 'key-city-alias-almaty'),
            ('Astana', 'Астана', 'key-city-alias-astana'),
            ('Алматы', 'Алматы', 'key-city-cyrillic-almaty'),
            ('алматы', 'Алматы', 'key-city-cyrillic-almaty-lower'),
            ('Жезказган', 'Жезказган', 'key-city-zhezkazgan'),
        )
        for city, expected, key in accepted:
            response, _, _ = self._post(city=city, idempotency_key=key)
            self.assertEqual(response.status_code, 200, response.content)
            req = Request.objects.get(idempotency_key=key)
            self.assertEqual(req.city, expected)

        legacy, _, _ = self._post(city='Аматы', idempotency_key='key-city-alias-typo')
        self.assertEqual(legacy.status_code, 200, legacy.content)
        self.assertEqual(
            Request.objects.get(idempotency_key='key-city-alias-typo').city,
            'Алматы',
        )

        before = Request.objects.count()
        unknown, _, _ = self._post(
            city='Город-которого-нет',
            idempotency_key='key-city-alias-unknown',
        )
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()['fields']['city'], 'Выберите город из списка.')
        self.assertEqual(Request.objects.count(), before)

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
        PartCategory.objects.create(name='Двигатель')
        response, _, _ = self._post(
            query='ремень ГРМ',
            brand='Toyota',
            model='CustomWagon',
            brand_id=str(self.core_brand.id),
            model_id='',
            category='Двигатель',
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
        second = _seller(
            name='Car Almaty Second',
            whatsapp='77010000022',
            city='Алматы',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            country_fk=self.japan,
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
        second_dispatch = RequestDispatch.objects.get(request=req, seller=second)
        self.assertEqual(second_dispatch.status, RequestDispatch.STATUS_SENT)

    def test_repeat_post_does_not_duplicate(self):
        first, buyer_wa, instagram = self._post(idempotency_key='same-key')
        second, buyer_wa2, instagram2 = self._post(idempotency_key='same-key')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(RequestDispatch.objects.count(), 1)
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
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            country_fk=self.japan,
        )
        _seller(
            name='Plus prefix shop',
            whatsapp='+77010000001',
            city='Караганда',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            country_fk=self.japan,
        )
        _seller(
            name='Invalid number shop',
            whatsapp='00000000000',
            city='Актобе',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            country_fk=self.japan,
        )
        response, _, _ = self._post(idempotency_key='key-dup-wa')
        self.assertEqual(response.status_code, 200, response.content)
        from core.phone_utils import normalize_phone_for_whatsapp
        phones = [
            normalize_phone_for_whatsapp(item.seller.whatsapp)
            for item in RequestDispatch.objects.select_related('seller')
        ]
        self.assertEqual(phones.count('77010000001'), 1)
        self.assertEqual(set(phones), {'77010000001'})
        self.assertNotIn(self.live_truck.id, RequestDispatch.objects.values_list('seller_id', flat=True))
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
            'transport_type': 'car',
            'category': 'Тормоза',
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
        buyer = Request.objects.get().buyer_contact
        self.assertIsNotNone(buyer)
        self.assertFalse(
            ContactConsent.objects.filter(
                buyer=buyer,
                purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
                status=CONTACT_CONSENT_STATUS_GRANTED,
            ).exists()
        )
        service = ContactConsent.objects.get(
            buyer=buyer,
            purpose=CONTACT_CONSENT_PURPOSE_SERVICE,
        )
        self.assertEqual(service.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(service.source, CONTACT_CONSENT_SOURCE_REQUEST_FORM)
        self.assertEqual(service.consent_text_version, HOME_PARTS_CONSENT_TEXT_VERSION)
        self.assertIsNotNone(service.consented_at)
        self.assertTrue(service.evidence_reference.startswith('home_parts_request:'))

    def test_submit_without_consent_field_records_service_consent(self):
        response, _, _ = self._post(idempotency_key='key-no-checkbox')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            ContactConsent.objects.filter(
                purpose=CONTACT_CONSENT_PURPOSE_SERVICE,
                status=CONTACT_CONSENT_STATUS_GRANTED,
            ).count(),
            1,
        )

    def test_vehicle_suggest_does_not_record_consent(self):
        response = self.client.get('/api/vehicle-suggest/', {'kind': 'brand', 'q': 'toy'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ContactConsent.objects.exists())

    def test_existing_revoked_service_consent_is_not_overwritten(self):
        first, _, _ = self._post(idempotency_key='key-consent-first')
        self.assertEqual(first.status_code, 200, first.content)
        buyer = Request.objects.get().buyer_contact
        consent = buyer.consents.get(purpose=CONTACT_CONSENT_PURPOSE_SERVICE)
        revoked_at = timezone.now()
        consent.status = CONTACT_CONSENT_STATUS_REVOKED
        consent.revoked_at = revoked_at
        consent.save(update_fields=['status', 'revoked_at', 'updated_at'])
        second, _, _ = self._post(
            query='масляный фильтр',
            idempotency_key='key-consent-second',
        )
        self.assertEqual(second.status_code, 200, second.content)
        consent.refresh_from_db()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.assertEqual(consent.revoked_at, revoked_at)

    def test_home_result_shows_relevant_hits_not_random_showcase(self):
        Product.objects.create(
            title='тормозные колодки Camry',
            slug='pads-result-hit',
            article='PAD-HIT',
            price=1000,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
            brand=self.catalog_brand,
            car_model=self.catalog_model,
        )
        Product.objects.create(
            title='Случайный товар витрины XYZ',
            slug='random-showcase-xyz',
            article='RND-XYZ',
            price=9000,
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            status='active',
        )
        response, _, _ = self._post(idempotency_key='key-result-hits')
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get()
        page = self.client.get('/', {'home_request': str(req.access_token)})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'тормозные колодки Camry')
        self.assertContains(page, 'id="home-request-result"')
        self.assertNotContains(page, 'Случайный товар витрины XYZ')
        html = page.content.decode()
        self.assertNotIn('id="catalog-results"', html)


@override_settings(
    PUBLIC_BASE_URL='https://zpt.kz',
    HOME_PARTS_MAX_PER_HOUR=0,
    HOME_PARTS_MAX_PER_PHONE_HOUR=0,
)
class HomeShortMatchedSellerTests(TestCase):
    """home_short uses the existing MATCHED seller selection, not ALL_KZ."""

    def setUp(self):
        self.client = Client()
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )
        PartCategory.objects.create(name='Трансмиссия')
        PartCategory.objects.create(name='Тормоза')
        PartCategory.objects.create(name='Кузов')
        self.brand, self.model = _core_vehicle(
            'Mercedes-Benz',
            '123',
            country='Германия',
        )
        self.germany = self.brand.country
        _core_vehicle('Mercedes-Benz', 'E-Class', country='Германия')
        self.exact = self._specialist(
            name='Exact Almaty',
            whatsapp='77020000001',
            city='Алматы',
            brand='Mercedes-Benz',
            model='123',
        )
        self.all_spec = self._specialist(
            name='All brands',
            whatsapp='77020000002',
            city='Астана',
            brand='',
            model='',
            all_brands=True,
            all_models=True,
            all_countries=True,
        )
        self.other_brand = self._specialist(
            name='Other brand',
            whatsapp='77020000003',
            city='Алматы',
            brand='BMW',
            model='X5',
        )
        self.other_category = self._specialist(
            name='Other category',
            whatsapp='77020000004',
            city='Алматы',
            brand='Mercedes-Benz',
            model='123',
            category='Тормоза',
        )
        self.other_city = self._specialist(
            name='Other city',
            whatsapp='77020000005',
            city='Шымкент',
            brand='Mercedes-Benz',
            model='123',
        )
        self.brand_level = self._specialist(
            name='Brand fallback',
            whatsapp='77020000006',
            city='Алматы',
            brand='Mercedes-Benz',
            model='E-Class',
        )
        self.truck = self._specialist(
            name='Truck specialist',
            whatsapp='77020000007',
            city='Алматы',
            transport_type='truck',
            all_brands=True,
            all_models=True,
            all_countries=True,
            brand='',
            model='',
        )

    def _specialist(self, **kwargs):
        defaults = {
            'transport_type': 'car',
            'category': 'Трансмиссия',
            'country_fk': self.germany,
        }
        defaults.update(kwargs)
        return _seller(**defaults)

    def _post(self, **extra):
        data = {
            'query': 'привод граната',
            'brand': 'Mercedes-Benz',
            'model': '123',
            'brand_id': str(self.brand.id),
            'model_id': str(self.model.id),
            'city': 'Алматы',
            'phone': '87015556677',
            'transport_type': 'car',
            'category': 'Трансмиссия',
            'idempotency_key': extra.pop('idempotency_key', 'key-matched-1'),
        }
        data.update(extra)
        with patch('core.views._send_buyer_whatsapp_notification_async'), patch(
            'core.views.schedule_instagram_publication_for_request',
        ):
            with self.captureOnCommitCallbacks(execute=True):
                return self.client.post('/api/home-parts-request/', data=data)

    def _seller_ids(self):
        return set(RequestDispatch.objects.values_list('seller_id', flat=True))

    def test_home_short_is_city_first_and_canonical_category(self):
        response = self._post(category='трансмиссия', idempotency_key='key-canon')
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get()
        self.assertEqual(req.source, Request.SOURCE_HOME_SHORT)
        self.assertEqual(req.dispatch_mode, Request.DISPATCH_MODE_MATCHED)
        self.assertEqual(req.search_scope, 'city')
        self.assertEqual(req.transport_type, 'car')
        self.assertEqual(req.category, 'Трансмиссия')
        self.assertEqual(req.brand, 'Mercedes-Benz')
        self.assertEqual(req.model, '123')
        self.assertEqual(req.country, 'Германия')

    def test_exact_all_brands_and_other_city_match_wrong_sellers_do_not(self):
        response = self._post(idempotency_key='key-exact-tier')
        self.assertEqual(response.status_code, 200, response.content)
        seller_ids = self._seller_ids()
        self.assertEqual(
            seller_ids,
            {self.exact.id, self.all_spec.id},
        )
        self.assertNotIn(self.truck.id, seller_ids)
        self.assertNotIn(self.other_category.id, seller_ids)
        self.assertNotIn(self.other_brand.id, seller_ids)
        self.assertNotIn(self.brand_level.id, seller_ids)

    def test_brand_fallback_when_exact_model_seller_is_absent(self):
        self.exact.is_active = False
        self.exact.save(update_fields=['is_active'])
        self.all_spec.is_active = False
        self.all_spec.save(update_fields=['is_active'])
        self.other_city.is_active = False
        self.other_city.save(update_fields=['is_active'])
        response = self._post(idempotency_key='key-fallback')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self._seller_ids(), {self.brand_level.id})

    def test_article_only_keeps_transport_and_category_limits(self):
        body = _seller(
            name='Body Almaty',
            whatsapp='77020000011',
            city='Алматы',
            transport_type='car',
            category='Кузов',
            brand='',
            model='',
        )
        body_other_city = _seller(
            name='Body Shymkent',
            whatsapp='77020000012',
            city='Шымкент',
            transport_type='car',
            category='Кузов',
            brand='Toyota',
            model='Camry',
        )
        truck_body = _seller(
            name='Truck body',
            whatsapp='77020000013',
            city='Алматы',
            transport_type='truck',
            category='Кузов',
            all_brands=True,
            all_models=True,
            all_countries=True,
        )
        response = self._post(
            query='52119-0K040',
            brand='',
            model='',
            brand_id='',
            model_id='',
            transport_type='car',
            category='Кузов',
            idempotency_key='key-article-limit',
        )
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get()
        self.assertEqual(req.article, '52119-0K040')
        self.assertEqual(req.brand, '')
        self.assertEqual(req.model, '')
        self.assertEqual(req.transport_type, 'car')
        self.assertEqual(req.category, 'Кузов')
        self.assertEqual(self._seller_ids(), {body.id})
        self.assertNotIn(truck_body.id, self._seller_ids())
        self.assertNotIn(self.exact.id, self._seller_ids())

    def test_duplicate_whatsapp_creates_one_home_short_dispatch(self):
        Seller.objects.update(is_active=False)
        _seller(
            name='Shop eight',
            whatsapp='87013334455',
            city='Алматы',
            transport_type='car',
            category='Трансмиссия',
            all_brands=True,
            all_models=True,
            all_countries=True,
        )
        _seller(
            name='Shop plus',
            whatsapp='+77013334455',
            city='Астана',
            transport_type='car',
            category='Трансмиссия',
            all_brands=True,
            all_models=True,
            all_countries=True,
        )
        response = self._post(idempotency_key='key-wa-dedup')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(RequestDispatch.objects.count(), 1)
        self.assertEqual(Request.objects.get().source, Request.SOURCE_HOME_SHORT)

    def test_broadcast_off_test_and_emergency_stop_still_apply(self):
        _ensure_broadcast_settings(mode=BroadcastSettings.MODE_OFF, emergency_stop=False)
        off = self._post(idempotency_key='key-off-matched')
        self.assertEqual(off.status_code, 200, off.content)
        self.assertFalse(off.json()['queued'])
        self.assertEqual(RequestDispatch.objects.count(), 0)
        self.assertEqual(Request.objects.get().dispatch_mode, Request.DISPATCH_MODE_MATCHED)

        test_seller = self._specialist(
            name='Matched test seller',
            whatsapp='77020000021',
            is_test_seller=True,
            receive_requests=False,
            all_brands=True,
            all_models=True,
            all_countries=True,
            brand='',
            model='',
        )
        _ensure_broadcast_settings(mode=BroadcastSettings.MODE_TEST, emergency_stop=False)
        test_mode = self._post(idempotency_key='key-test-matched')
        self.assertEqual(test_mode.status_code, 200, test_mode.content)
        self.assertEqual(
            set(RequestDispatch.objects.filter(
                request__idempotency_key='key-test-matched',
            ).values_list('seller_id', flat=True)),
            {test_seller.id},
        )

        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            emergency_stop=True,
        )
        stopped = self._post(idempotency_key='key-stop-matched')
        self.assertEqual(stopped.status_code, 200, stopped.content)
        self.assertFalse(stopped.json()['queued'])
        self.assertFalse(
            RequestDispatch.objects.filter(
                request__idempotency_key='key-stop-matched',
            ).exists()
        )

    def test_invalid_transport_category_and_vehicle_mismatch_rejected(self):
        invalid_transport = self._post(
            transport_type='bus',
            idempotency_key='key-bad-transport',
        )
        self.assertEqual(invalid_transport.status_code, 400)
        self.assertIn('transport_type', invalid_transport.json()['fields'])

        missing_category = self._post(category='', idempotency_key='key-no-category')
        self.assertEqual(missing_category.status_code, 400)
        self.assertEqual(
            missing_category.json()['fields']['category'],
            'Выберите категорию запчасти.',
        )

        unknown_category = self._post(
            category='Несуществующая',
            idempotency_key='key-unknown-category',
        )
        self.assertEqual(unknown_category.status_code, 400)
        self.assertEqual(
            unknown_category.json()['fields']['category'],
            'Выберите категорию из списка.',
        )

        truck_brand, truck_model = _core_vehicle(
            'KAMAZ',
            '65115',
            transport_type='truck',
            country='Россия',
        )
        mismatch = self._post(
            transport_type='car',
            brand='KAMAZ',
            model='65115',
            brand_id=str(truck_brand.id),
            model_id=str(truck_model.id),
            idempotency_key='key-mismatch',
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn('не соответствует', mismatch.json()['error'])
        self.assertIn('brand', mismatch.json()['fields'])
        name_mismatch = self._post(
            transport_type='car',
            brand='KAMAZ',
            model='65115',
            brand_id='',
            model_id='',
            idempotency_key='key-mismatch-name',
        )
        self.assertEqual(name_mismatch.status_code, 400)
        self.assertEqual(Request.objects.count(), 0)

    def test_idempotency_replay_does_not_duplicate_request_or_dispatch(self):
        first = self._post(idempotency_key='key-replay-matched')
        dispatch_count = RequestDispatch.objects.count()
        second = self._post(idempotency_key='key-replay-matched')
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.json()['replay'])
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(RequestDispatch.objects.count(), dispatch_count)
        self.assertGreater(dispatch_count, 0)

    def test_home_form_is_compact_matched_version(self):
        page = self.client.get('/')
        self.assertContains(page, 'home-parts-form-v8.js')
        self.assertNotContains(page, 'home-parts-form-v7.js')
        self.assertNotContains(page, 'home-parts-form-v6.js')
        self.assertNotContains(page, 'home-parts-form-v5.js')
        self.assertNotContains(page, 'home-parts-form-v4.js')
        self.assertContains(page, 'Название, артикул или описание запчасти')
        self.assertContains(page, 'id="home-parts-warning"')
        self.assertContains(page, 'id="home-vehicle-picker"')
        self.assertContains(page, 'id="home-brand-open"')
        self.assertContains(page, 'id="home-model-open"')
        from pathlib import Path
        script = Path('static/js/home-parts-form-v8.js').read_text(encoding='utf-8')
        rejection_ui = script.split('function showRejection', 1)[1].split('function ', 1)[0]
        self.assertIn('Исправить запрос', rejection_ui)
        self.assertNotIn('Всё равно отправить', rejection_ui)
        self.assertIn("openVehiclePicker('brand')", script)
        self.assertIn("openVehiclePicker('model')", script)
        self.assertContains(page, 'name="transport_type"')
        self.assertContains(page, 'value="car" checked')
        self.assertContains(page, 'Легковые')
        self.assertContains(page, 'Грузовые')
        self.assertContains(page, 'name="category"')
        self.assertContains(page, '>Трансмиссия</option>')
        self.assertContains(page, 'Ищем подходящих продавцов по всему Казахстану')
        self.assertContains(page, '>Дополнительно</summary>')
        self.assertContains(page, 'подходящие продавцы по Казахстану')
        self.assertNotContains(page, 'более 300')
        self.assertNotContains(page, 'name="search_scope"')
        self.assertNotContains(page, 'Выберите страну')

    def test_category_mismatch_warns_without_creating_request(self):
        response = self._post(
            query='колодки',
            category='Трансмиссия',
            idempotency_key='key-warn-pads',
        )
        self.assertEqual(response.status_code, 422, response.content)
        payload = response.json()
        self.assertTrue(payload['warning_required'])
        self.assertEqual(payload['warning_code'], 'category_mismatch')
        self.assertEqual(payload['suggested_category'], 'Тормоза')
        self.assertEqual(
            payload['warning_message'],
            'По тексту запроса похоже, что вам нужна категория “Тормоза”, '
            'а выбрана “Трансмиссия”. Проверьте категорию.',
        )
        self.assertNotIn('id', payload)
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(RequestDispatch.objects.count(), 0)

    def test_confirmed_category_mismatch_creates_one_request(self):
        warned = self._post(
            query='колодки',
            category='Трансмиссия',
            idempotency_key='key-warn-confirm',
        )
        self.assertEqual(warned.status_code, 422, warned.content)
        self.assertEqual(Request.objects.count(), 0)

        created = self._post(
            query='колодки',
            category='Трансмиссия',
            idempotency_key='key-warn-confirm',
            warning_confirmed='1',
        )
        self.assertEqual(created.status_code, 200, created.content)
        self.assertFalse(created.json().get('warning_required', False))
        self.assertFalse(created.json()['replay'])
        req = Request.objects.get()
        self.assertEqual(req.category, 'Трансмиссия')
        dispatch_count = RequestDispatch.objects.count()

        replay = self._post(
            query='колодки',
            category='Трансмиссия',
            idempotency_key='key-warn-confirm',
            warning_confirmed='1',
        )
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.json()['replay'])
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(RequestDispatch.objects.count(), dispatch_count)

        without_flag = self._post(
            query='колодки',
            category='Трансмиссия',
            idempotency_key='key-warn-confirm',
        )
        self.assertEqual(without_flag.status_code, 200, without_flag.content)
        self.assertTrue(without_flag.json()['replay'])
        self.assertEqual(Request.objects.count(), 1)

    def test_matching_brake_and_transmission_queries_do_not_warn(self):
        pads = self._post(
            query='колодки',
            category='Тормоза',
            idempotency_key='key-pads-ok',
        )
        self.assertEqual(pads.status_code, 200, pads.content)
        self.assertNotIn('warning_required', pads.json())

        drive = self._post(
            query='привод',
            category='Трансмиссия',
            idempotency_key='key-drive-ok',
        )
        self.assertEqual(drive.status_code, 200, drive.content)
        self.assertNotIn('warning_required', drive.json())
        self.assertEqual(Request.objects.count(), 2)

    def test_drive_with_brakes_category_suggests_transmission(self):
        response = self._post(
            query='привод',
            category='Тормоза',
            idempotency_key='key-drive-brakes',
        )
        self.assertEqual(response.status_code, 422, response.content)
        payload = response.json()
        self.assertEqual(payload['warning_code'], 'category_mismatch')
        self.assertEqual(payload['suggested_category'], 'Трансмиссия')
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(RequestDispatch.objects.count(), 0)

    def test_exact_article_skips_category_warning(self):
        response = self._post(
            query='52119-0K040',
            category='Трансмиссия',
            brand='',
            model='',
            brand_id='',
            model_id='',
            idempotency_key='key-article-no-warn',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('warning_required', response.json())
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(Request.objects.get().article, '52119-0K040')

    def test_service_intent_rejects_without_creating_request(self):
        for query, key in (
            ('записаться на СТО', 'key-sto'),
            ('шиномонтаж', 'key-tires'),
            ('продам автомобиль', 'key-sell-car'),
            ('страхование', 'key-insurance'),
            ('реклама', 'key-ads'),
        ):
            with self.subTest(query=query):
                response = self._post(
                    query=query,
                    category='Тормоза',
                    idempotency_key=key,
                    warning_confirmed='1',
                )
                self.assertEqual(response.status_code, 422, response.content)
                payload = response.json()
                self.assertTrue(payload['rejected'])
                self.assertEqual(payload['rejection_code'], 'not_parts_request')
                self.assertNotIn('warning_required', payload)
                self.assertNotIn('suggested_category', payload)
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(RequestDispatch.objects.count(), 0)

    def test_pads_with_installation_is_category_hint_not_non_parts(self):
        mismatch = self._post(
            query='нужны тормозные колодки с установкой',
            category='Трансмиссия',
            idempotency_key='key-pads-install',
        )
        self.assertEqual(mismatch.status_code, 422, mismatch.content)
        payload = mismatch.json()
        self.assertEqual(payload['warning_code'], 'category_mismatch')
        self.assertEqual(payload['suggested_category'], 'Тормоза')
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(RequestDispatch.objects.count(), 0)

        genitive = self._post(
            query='нужна замена тормозных колодок',
            category='Кузов',
            idempotency_key='key-pads-genitive',
        )
        self.assertEqual(genitive.status_code, 422, genitive.content)
        self.assertEqual(genitive.json()['warning_code'], 'category_mismatch')
        self.assertEqual(genitive.json()['suggested_category'], 'Тормоза')

        matched = self._post(
            query='нужны тормозные колодки с установкой',
            category='Тормоза',
            idempotency_key='key-pads-install-ok',
        )
        self.assertEqual(matched.status_code, 200, matched.content)
        self.assertNotIn('warning_required', matched.json())
        self.assertEqual(Request.objects.count(), 1)

    @override_settings(HOME_PARTS_MAX_PER_HOUR=1, HOME_PARTS_MAX_PER_PHONE_HOUR=1)
    def test_warning_response_does_not_consume_rate_limit(self):
        warned = self._post(
            query='колодки',
            category='Трансмиссия',
            idempotency_key='key-warn-rate',
        )
        self.assertEqual(warned.status_code, 422, warned.content)
        created = self._post(
            query='колодки',
            category='Трансмиссия',
            idempotency_key='key-warn-rate',
            warning_confirmed='1',
        )
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(Request.objects.count(), 1)

    def test_unknown_part_name_is_not_rejected(self):
        response = self._post(
            query='подушка двигателя',
            category='Тормоза',
            idempotency_key='key-engine-mount',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('rejected', response.json())
        self.assertEqual(Request.objects.count(), 1)

    def test_radiator_and_steering_rack_suggest_their_categories(self):
        PartCategory.objects.create(name='Охлаждение')
        radiator = self._post(
            query='радиатор',
            category='Трансмиссия',
            idempotency_key='key-radiator',
        )
        self.assertEqual(radiator.status_code, 422, radiator.content)
        self.assertEqual(radiator.json()['warning_code'], 'category_mismatch')
        self.assertEqual(radiator.json()['suggested_category'], 'Охлаждение')
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(RequestDispatch.objects.count(), 0)

        rack = self._post(
            query='рулевая рейка',
            category='Охлаждение',
            idempotency_key='key-rack',
        )
        self.assertEqual(rack.status_code, 422, rack.content)
        self.assertEqual(rack.json()['suggested_category'], 'Рулевое управление')
        self.assertEqual(Request.objects.count(), 0)

    def test_bare_steering_wheel_warns_without_guessing_category(self):
        PartCategory.objects.create(name='Охлаждение')
        response = self._post(
            query='руль',
            category='Охлаждение',
            idempotency_key='key-wheel',
        )
        self.assertEqual(response.status_code, 422, response.content)
        payload = response.json()
        self.assertTrue(payload['warning_required'])
        self.assertEqual(payload['warning_code'], 'ambiguous_part_category')
        self.assertNotIn('suggested_category', payload)
        self.assertNotIn('rejected', payload)
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(RequestDispatch.objects.count(), 0)

    def test_multiple_part_categories_warn_until_confirmed(self):
        warned = self._post(
            query='колодки, амортизатор',
            category='Тормоза',
            idempotency_key='key-multi-cat',
        )
        self.assertEqual(warned.status_code, 422, warned.content)
        payload = warned.json()
        self.assertEqual(payload['warning_code'], 'multiple_part_categories')
        self.assertNotIn('suggested_category', payload)
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(RequestDispatch.objects.count(), 0)

        same_category = self._post(
            query='колодки, тормозные диски',
            category='Тормоза',
            idempotency_key='key-same-cat',
        )
        self.assertEqual(same_category.status_code, 200, same_category.content)

        created = self._post(
            query='колодки, амортизатор',
            category='Тормоза',
            idempotency_key='key-multi-cat',
            warning_confirmed='1',
        )
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(Request.objects.filter(description__icontains='амортизатор').count(), 1)
        replay = self._post(
            query='колодки, амортизатор',
            category='Тормоза',
            idempotency_key='key-multi-cat',
            warning_confirmed='1',
        )
        self.assertTrue(replay.json()['replay'])
        self.assertEqual(Request.objects.filter(description__icontains='амортизатор').count(), 1)

    def test_meaningless_text_is_rejected(self):
        for query, key in (
            ('помогите', 'key-help'),
            ('сломалось', 'key-broken'),
            ('нужна запчасть', 'key-generic-part'),
        ):
            with self.subTest(query=query):
                response = self._post(
                    query=query,
                    category='Тормоза',
                    idempotency_key=key,
                    warning_confirmed='1',
                )
                self.assertEqual(response.status_code, 422, response.content)
                payload = response.json()
                self.assertTrue(payload['rejected'])
                self.assertEqual(payload['rejection_code'], 'unspecified_part')
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(RequestDispatch.objects.count(), 0)

    def test_reject_does_not_consume_idempotency_key_or_rate_limit(self):
        with self.settings(HOME_PARTS_MAX_PER_HOUR=1, HOME_PARTS_MAX_PER_PHONE_HOUR=1):
            rejected = self._post(
                query='шиномонтаж',
                category='Тормоза',
                idempotency_key='key-reject-then-create',
            )
            self.assertEqual(rejected.status_code, 422, rejected.content)
            self.assertTrue(rejected.json()['rejected'])
            self.assertEqual(Request.objects.count(), 0)
            created = self._post(
                query='колодки',
                category='Тормоза',
                idempotency_key='key-reject-then-create',
            )
            self.assertEqual(created.status_code, 200, created.content)
            self.assertEqual(Request.objects.count(), 1)


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

    def test_transport_type_filters_brands_and_models_without_changing_default(self):
        car_brand, _car_model = _core_vehicle(
            'Volvo',
            'XC90',
            transport_type='car',
            country='Швеция',
        )
        truck_brand, _truck_model = _core_vehicle(
            'Volvo',
            'FH',
            transport_type='truck',
            country='Швеция',
        )

        car = self.client.get(
            '/api/vehicle-suggest/',
            {'kind': 'brand', 'q': 'volvo', 'transport_type': 'car'},
        )
        car_ids = [item['id'] for item in car.json()['items']]
        self.assertIn(car_brand.id, car_ids)
        self.assertNotIn(truck_brand.id, car_ids)

        truck = self.client.get(
            '/api/vehicle-suggest/',
            {'kind': 'brand', 'q': 'volvo', 'transport_type': 'truck'},
        )
        truck_ids = [item['id'] for item in truck.json()['items']]
        self.assertIn(truck_brand.id, truck_ids)
        self.assertNotIn(car_brand.id, truck_ids)

        omitted = self.client.get('/api/vehicle-suggest/', {'kind': 'brand', 'q': 'volvo'})
        omitted_ids = [item['id'] for item in omitted.json()['items']]
        self.assertIn(car_brand.id, omitted_ids)
        self.assertIn(truck_brand.id, omitted_ids)

        kamaz_car = self.client.get(
            '/api/vehicle-suggest/',
            {'kind': 'brand', 'q': 'kamaz', 'transport_type': 'car'},
        )
        self.assertEqual(kamaz_car.json()['items'], [])
        kamaz_any = self.client.get('/api/vehicle-suggest/', {'kind': 'brand', 'q': 'kamaz'})
        self.assertIn('KAMAZ', [item['name'] for item in kamaz_any.json()['items']])

        car_models = self.client.get(
            '/api/vehicle-suggest/',
            {'kind': 'model', 'q': '', 'brand': 'Volvo', 'transport_type': 'car'},
        )
        car_model_names = [item['name'] for item in car_models.json()['items']]
        self.assertIn('XC90', car_model_names)
        self.assertNotIn('FH', car_model_names)

        truck_models = self.client.get(
            '/api/vehicle-suggest/',
            {'kind': 'model', 'q': '', 'brand': 'Volvo', 'transport_type': 'truck'},
        )
        truck_model_names = [item['name'] for item in truck_models.json()['items']]
        self.assertIn('FH', truck_model_names)
        self.assertNotIn('XC90', truck_model_names)

        all_models = self.client.get(
            '/api/vehicle-suggest/',
            {'kind': 'model', 'q': '', 'brand': 'Volvo'},
        )
        all_model_names = [item['name'] for item in all_models.json()['items']]
        self.assertIn('XC90', all_model_names)
        self.assertIn('FH', all_model_names)


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


