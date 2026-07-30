import json
from unittest.mock import patch

from django.test import Client, TestCase

from core.models import Brand, BroadcastSettings, CarModel, Country, Request, Seller
from core.tests.test_request_dispatch_waves import _ensure_broadcast_settings
from core.views import _find_matching_sellers, _normalize_request_model


def _post_create_request(client, **extra):
    payload = {
        'transport_type': 'car',
        'country': 'Европа',
        'brand': 'Land Rover',
        'model': 'Range Rover',
        'category': 'Тормоза',
        'city': 'Алматы',
        'phone': '77001112233',
        'search_scope': 'city',
    }
    payload.update(extra)
    with patch('core.views._send_buyer_whatsapp_notification_async'), patch(
        'core.views._find_matching_sellers',
        return_value=([], 'no_match'),
    ):
        return client.post('/api/create-request/', data=payload)


@patch('core.views._send_buyer_whatsapp_notification_async')
@patch('core.views._find_matching_sellers', return_value=([], 'no_match'))
class CustomRequestModelCreateTests(TestCase):
    def setUp(self):
        self.client = Client()
        _ensure_broadcast_settings(mode=BroadcastSettings.MODE_LIVE)

    def test_catalog_model_still_works(self, *_mocks):
        response = _post_create_request(
            self.client,
            model='Range Rover',
        )
        self.assertEqual(response.status_code, 200)
        req = Request.objects.latest('id')
        self.assertEqual(req.model, 'Range Rover')

    def test_custom_model_saved_in_request(self, *_mocks):
        response = _post_create_request(
            self.client,
            model='Discovery Sport',
        )
        self.assertEqual(response.status_code, 200)
        req = Request.objects.latest('id')
        self.assertEqual(req.model, 'Discovery Sport')

    def test_technical_custom_marker_rejected(self, *_mocks):
        response = _post_create_request(
            self.client,
            model='__custom_model__',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
        self.assertEqual(Request.objects.count(), 0)

    def test_custom_label_text_rejected(self, *_mocks):
        response = _post_create_request(
            self.client,
            model='Моей модели нет в списке',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Request.objects.count(), 0)

    def test_empty_custom_model_rejected(self, *_mocks):
        response = _post_create_request(
            self.client,
            model='   ',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Request.objects.count(), 0)

    def test_model_longer_than_100_chars_rejected(self, *_mocks):
        response = _post_create_request(
            self.client,
            model='A' * 101,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Request.objects.count(), 0)

    def test_custom_model_does_not_create_carmodel(self, *_mocks):
        before = CarModel.objects.count()
        response = _post_create_request(
            self.client,
            model='Discovery Sport',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CarModel.objects.count(), before)

    def test_repeat_request_payload_with_unknown_model_is_accepted(self, *_mocks):
        response = _post_create_request(
            self.client,
            country='Европа',
            brand='Land Rover',
            model='Discovery Sport',
        )
        self.assertEqual(response.status_code, 200)
        req = Request.objects.latest('id')
        self.assertEqual(req.brand, 'Land Rover')
        self.assertEqual(req.model, 'Discovery Sport')


class CustomRequestModelNormalizationTests(TestCase):
    def test_strips_whitespace(self):
        self.assertEqual(
            _normalize_request_model('  Discovery Sport  '),
            'Discovery Sport',
        )


class CustomRequestModelSellerMatchingTests(TestCase):
    def setUp(self):
        _ensure_broadcast_settings(mode=BroadcastSettings.MODE_LIVE)

    def _make_request(self, **kwargs):
        defaults = {
            'transport_type': 'car',
            'country': 'Европа',
            'brand': 'Land Rover',
            'model': 'Discovery Sport',
            'category': 'Тормоза',
            'city': 'Алматы',
            'search_scope': 'city',
            'phone': '77001112233',
        }
        defaults.update(kwargs)
        return Request(**defaults)

    def test_custom_model_falls_back_to_brand_match(self):
        seller = Seller.objects.create(
            name='LR Brand Seller',
            whatsapp='77011112233',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
            all_countries=True,
            brand='Land Rover',
            category='Тормоза',
        )
        req = self._make_request()
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertIn(seller, list(sellers))

    def test_all_models_seller_matches_custom_model(self):
        seller = Seller.objects.create(
            name='All Models Seller',
            whatsapp='77022223344',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
            all_countries=True,
            brand='Land Rover',
            all_models=True,
            category='Тормоза',
        )
        req = self._make_request()
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertIn(seller, list(sellers))

    def test_seller_notification_uses_custom_model_text(self):
        req = Request.objects.create(
            transport_type='car',
            brand='Land Rover',
            model='Discovery Sport',
            category='Тормоза',
            city='Алматы',
            phone='77001112233',
        )
        from core.views import _seller_notification_text

        text = _seller_notification_text(req)
        self.assertIn('Марка: Land Rover', text)
        self.assertIn('Модель: Discovery Sport', text)
        self.assertNotIn('Моей модели нет в списке', text)


class CustomRequestModelApiTests(TestCase):
    """Backend/API часть сценария «марка без моделей в справочнике»."""

    def setUp(self):
        self.client = Client()
        _ensure_broadcast_settings(mode=BroadcastSettings.MODE_LIVE)
        self.country = Country.objects.create(name='Европа')
        self.brand = Brand.objects.create(
            country=self.country,
            name='Brand Without Models',
            transport_type='car',
        )

    def test_models_by_brand_returns_empty_array_for_brand_without_models(self):
        response = self.client.get(
            '/api/models-by-brand/',
            {'brand_id': self.brand.id, 'transport_type': 'car'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), [])
        self.assertEqual(
            CarModel.objects.filter(brand=self.brand, transport_type='car').count(),
            0,
        )

    @patch('core.views._send_buyer_whatsapp_notification_async')
    @patch('core.views._find_matching_sellers', return_value=([], 'no_match'))
    def test_manual_model_submitted_as_model_when_brand_has_no_catalog_models(self, *_mocks):
        response = _post_create_request(
            self.client,
            country='Европа',
            brand='Brand Without Models',
            model='My Manual Model',
        )
        self.assertEqual(response.status_code, 200)
        req = Request.objects.latest('id')
        self.assertEqual(req.brand, 'Brand Without Models')
        self.assertEqual(req.model, 'My Manual Model')
        self.assertEqual(CarModel.objects.filter(brand=self.brand).count(), 0)
