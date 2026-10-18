"""Separate, strictly bounded routing for requests from the dismantler section."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import BroadcastSettings, PartCategory, Request, RequestDispatch, Seller
from core.request_dispatch_service import send_single_dispatch
from core.views import _find_matching_sellers


@override_settings(HOME_PARTS_MAX_PER_HOUR=0, HOME_PARTS_MAX_PER_PHONE_HOUR=0)
class DismantlerRequestScopeTests(TestCase):
    def setUp(self):
        BroadcastSettings.objects.update_or_create(
            pk=1,
            defaults={
                'mode': BroadcastSettings.MODE_LIVE,
                'wave_size': 10,
                'wave_interval_minutes': 5,
                'emergency_stop': False,
            },
        )
        PartCategory.objects.get_or_create(name='Кузов')
        common = {
            'transport_type': 'car',
            'category': 'Кузов',
            'is_active': True,
            'is_paused': False,
            'receive_requests': True,
            'sells_new_parts': True,
        }
        self.local_dismantler = Seller.objects.create(
            name='Almaty dismantler',
            whatsapp='77010000001',
            city='Алматы',
            is_dismantler=True,
            **common,
        )
        self.remote_dismantler = Seller.objects.create(
            name='Astana dismantler',
            whatsapp='77010000002',
            city='Астана',
            is_dismantler=True,
            **common,
        )
        self.local_regular = Seller.objects.create(
            name='Almaty parts store',
            whatsapp='77010000003',
            city='Алматы',
            is_dismantler=False,
            **common,
        )
        self.remote_regular = Seller.objects.create(
            name='Astana parts store',
            whatsapp='77010000004',
            city='Астана',
            is_dismantler=False,
            **common,
        )

    def _post(self, *, key, dismantlers=True, scope='kazakhstan', city='Алматы', **extra):
        data = {
            'query': '52119-0K040',
            'city': city,
            'phone': '77019991122',
            'transport_type': 'car',
            'category': 'Кузов',
            'idempotency_key': key,
        }
        if dismantlers:
            data.update(is_dismantler_request='1', search_scope=scope)
        data.update(extra)
        with patch('core.views._send_buyer_whatsapp_notification_async'), patch(
            'core.services.home_parts_request.search_catalog_safe',
            return_value=([], [], False),
        ):
            return self.client.post('/api/home-parts-request/', data=data)

    def _recipients(self, req):
        return set(
            RequestDispatch.objects.filter(request=req).values_list('seller_id', flat=True)
        )

    def test_dismantler_form_has_city_choice_but_regular_form_does_not(self):
        used = self.client.get('/find-part/?condition=used')
        self.assertEqual(used.status_code, 200)
        self.assertContains(used, 'name="is_dismantler_request" id="home-parts-condition-flag" value="1"')
        self.assertContains(used, 'name="search_scope" value="city"')
        self.assertContains(used, 'name="search_scope" value="kazakhstan" checked')
        self.assertContains(used, 'name="search_scope" value="custom"')
        self.assertContains(used, 'Только мой город')
        self.assertContains(used, 'Весь Казахстан')

        ordinary = self.client.get('/find-part/?request=1')
        self.assertEqual(ordinary.status_code, 200)
        self.assertContains(ordinary, 'name="is_dismantler_request" id="home-parts-condition-flag" value="0"')
        self.assertContains(ordinary, 'name="search_scope"')

    def test_used_parts_city_go_only_to_local_dismantlers(self):
        response = self._post(key='used-local', scope='city')
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get(idempotency_key='used-local')
        self.assertTrue(req.is_dismantler_request)
        self.assertEqual(req.search_scope, 'city')
        self.assertEqual(req.routing_strategy, Request.ROUTING_MATCHED_CITY)
        self.assertEqual(self._recipients(req), {self.local_dismantler.pk})
        self.assertIn('condition=used', response.json()['result_url'])

    def test_used_parts_kazakhstan_go_only_to_dismantlers_nationwide(self):
        response = self._post(key='used-all', scope='kazakhstan')
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get(idempotency_key='used-all')
        self.assertEqual(req.search_scope, 'kazakhstan')
        self.assertEqual(req.routing_strategy, Request.ROUTING_MATCHED_KAZAKHSTAN)
        self.assertEqual(
            self._recipients(req),
            {self.local_dismantler.pk, self.remote_dismantler.pk},
        )

    def test_city_only_never_falls_back_to_other_cities_or_regular_stores(self):
        self.local_dismantler.delete()
        response = self._post(key='used-city-no-match', scope='city')
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get(idempotency_key='used-city-no-match')
        self.assertEqual(req.routing_strategy, Request.ROUTING_NO_MATCH)
        self.assertEqual(req.status, 'no_sellers')
        self.assertEqual(self._recipients(req), set())

    def test_ordinary_short_form_remains_nationwide_with_regular_sellers(self):
        response = self._post(key='ordinary-kz', dismantlers=False)
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get(idempotency_key='ordinary-kz')
        self.assertFalse(req.is_dismantler_request)
        self.assertEqual(req.search_scope, 'kazakhstan')
        self.assertEqual(
            self._recipients(req),
            {
                self.local_dismantler.pk,
                self.remote_dismantler.pk,
                self.local_regular.pk,
                self.remote_regular.pk,
            },
        )

    def test_classic_city_fallback_stays_unchanged(self):
        req = Request.objects.create(
            source=Request.SOURCE_CLASSIC,
            transport_type='car',
            category='Кузов',
            city='Костанай',
            search_scope='city',
            phone='77010009999',
        )
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, Request.ROUTING_FALLBACK_KAZAKHSTAN)
        self.assertEqual(sellers.count(), 4)

    def test_invalid_scope_is_rejected_before_saving(self):
        response = self._post(key='used-invalid', scope='custom')
        self.assertEqual(response.status_code, 400)
        self.assertIn('selected_cities', response.json()['fields'])
        self.assertFalse(Request.objects.filter(idempotency_key='used-invalid').exists())

    def test_same_idempotency_key_cannot_change_scope(self):
        first = self._post(key='used-replay', scope='city')
        self.assertEqual(first.status_code, 200, first.content)
        second = self._post(key='used-replay', scope='city')
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.json()['replay'])
        other_scope = self._post(key='used-replay', scope='kazakhstan')
        self.assertEqual(other_scope.status_code, 409)
        self.assertEqual(Request.objects.filter(idempotency_key='used-replay').count(), 1)
        self.assertEqual(RequestDispatch.objects.count(), 1)

    def test_direct_sender_rejects_other_seller_types_and_cities(self):
        req = Request.objects.create(
            source=Request.SOURCE_HOME_SHORT,
            transport_type='car',
            category='Кузов',
            city='Алматы',
            is_dismantler_request=True,
            search_scope='city',
            phone='77019991122',
        )
        with patch('core.views.send_whatsapp_template') as whatsapp_send:
            for i, seller in enumerate(
                (self.local_regular, self.remote_dismantler), start=1
            ):
                dispatch = RequestDispatch.objects.create(
                    request=req,
                    seller=seller,
                    wave_number=1,
                    position_number=i,
                    scheduled_at=timezone.now(),
                )
                result = send_single_dispatch(dispatch)
                dispatch.refresh_from_db()
                self.assertEqual(result['reason'], 'dismantler_scope_mismatch')
                self.assertEqual(dispatch.status, RequestDispatch.STATUS_PAUSED)
            whatsapp_send.assert_not_called()
