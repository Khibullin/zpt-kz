"""Shared new/used buyer form, geography controls and verified recipient routing."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import BroadcastSettings, PartCategory, Request, RequestDispatch, Seller
from core.request_dispatch_service import send_single_dispatch


@override_settings(HOME_PARTS_MAX_PER_HOUR=0, HOME_PARTS_MAX_PER_PHONE_HOUR=0)
class UnifiedPartsFormTests(TestCase):
    def setUp(self):
        BroadcastSettings.objects.update_or_create(
            pk=1,
            defaults={
                'mode': BroadcastSettings.MODE_LIVE,
                'emergency_stop': False,
                'wave_size': 10,
                'wave_interval_minutes': 5,
            },
        )
        PartCategory.objects.create(name='Кузов')
        common = dict(
            category='Кузов',
            transport_type='car',
            all_brands=True,
            all_models=True,
            all_countries=True,
            receive_requests=True,
            is_active=True,
            is_paused=False,
        )
        self.new_local = Seller.objects.create(
            name='Verified new Almaty', whatsapp='77015550101', city='Алматы',
            sells_new_parts=True, is_dismantler=False, **common,
        )
        self.new_remote = Seller.objects.create(
            name='Verified new Astana', whatsapp='77015550102', city='Астана',
            sells_new_parts=True, is_dismantler=False, **common,
        )
        self.unverified = Seller.objects.create(
            name='Unverified seller', whatsapp='77015550103', city='Алматы',
            sells_new_parts=None, is_dismantler=False, **common,
        )
        self.used_local = Seller.objects.create(
            name='Used Almaty', whatsapp='77015550104', city='Алматы',
            sells_new_parts=False, is_dismantler=True, **common,
        )
        self.used_remote = Seller.objects.create(
            name='Used Karaganda', whatsapp='77015550105', city='Караганда',
            sells_new_parts=False, is_dismantler=True, **common,
        )
        self.both_remote = Seller.objects.create(
            name='Verified both', whatsapp='77015550106', city='Астана',
            sells_new_parts=True, is_dismantler=True, **common,
        )

    def _post(self, *, key, condition='new', scope='kazakhstan', cities=None, city='Алматы', **extra):
        data = {
            'query': '52119-0K040',
            'category': 'Кузов',
            'city': city,
            'phone': '77019995566',
            'transport_type': 'car',
            'part_condition': condition,
            'is_dismantler_request': '1' if condition == 'used' else '0',
            'search_scope': scope,
            'idempotency_key': key,
        }
        if cities is not None:
            data['selected_cities'] = cities
        data.update(extra)
        with patch('core.views._send_buyer_whatsapp_notification_async'), patch(
            'core.views.schedule_instagram_publication_for_request'
        ), patch(
            'core.services.home_parts_request.search_catalog_safe',
            return_value=([], [], False),
        ):
            return self.client.post('/api/home-parts-request/', data=data)

    def _recipients(self, key):
        req = Request.objects.get(idempotency_key=key)
        return req, set(
            RequestDispatch.objects.filter(request=req).values_list('seller_id', flat=True)
        )

    def test_form_copy_and_default_modes(self):
        normal = self.client.get('/find-part/?request=1')
        used = self.client.get('/find-part/?condition=used')
        self.assertEqual(normal.status_code, 200)
        self.assertEqual(used.status_code, 200)
        self.assertContains(normal, 'Новые запчасти')
        self.assertContains(used, 'Запчасти с авторазборов')
        self.assertContains(normal, 'Какая запчасть вам нужна?')
        self.assertContains(used, 'Какая запчасть вам нужна?')
        self.assertContains(used, 'Если знаете артикул, можете указать его.')
        self.assertNotContains(used, 'Б/У:')
        for response in (normal, used):
            self.assertContains(response, 'name="part_condition"')
            self.assertContains(response, 'name="search_scope" value="city"')
            self.assertContains(response, 'name="search_scope" value="custom"')
            self.assertContains(response, 'name="search_scope" value="kazakhstan" checked')
            self.assertContains(response, 'id="home-custom-cities"')
            self.assertContains(response, 'name="selected_cities"')
            self.assertContains(response, 'id="home-year"')
            self.assertNotContains(response, 'Выберите страну')
        self.assertContains(normal, 'id="home-parts-condition-flag" value="0"')
        self.assertContains(used, 'id="home-parts-condition-flag" value="1"')

    def test_new_parts_only_confirmed_or_confirmed_mixed(self):
        response = self._post(key='new-nationwide')
        self.assertEqual(response.status_code, 200, response.content)
        req, recipients = self._recipients('new-nationwide')
        self.assertFalse(req.is_dismantler_request)
        self.assertEqual(req.search_scope, 'kazakhstan')
        self.assertEqual(recipients, {
            self.new_local.pk, self.new_remote.pk, self.both_remote.pk,
        })
        self.assertNotIn(self.unverified.pk, recipients)
        self.assertNotIn(self.used_local.pk, recipients)

    def test_used_parts_only_dismantlers_including_mixed(self):
        response = self._post(key='used-nationwide', condition='used')
        self.assertEqual(response.status_code, 200, response.content)
        req, recipients = self._recipients('used-nationwide')
        self.assertTrue(req.is_dismantler_request)
        self.assertEqual(recipients, {
            self.used_local.pk, self.used_remote.pk, self.both_remote.pk,
        })
        self.assertNotIn(self.new_local.pk, recipients)

    def test_city_mode_is_strict_for_both_types(self):
        new = self._post(key='new-local', scope='city')
        used = self._post(key='used-local', condition='used', scope='city')
        self.assertEqual(new.status_code, 200, new.content)
        self.assertEqual(used.status_code, 200, used.content)
        _, new_sellers = self._recipients('new-local')
        _, used_sellers = self._recipients('used-local')
        self.assertEqual(new_sellers, {self.new_local.pk})
        self.assertEqual(used_sellers, {self.used_local.pk})

    def test_multi_city_respects_exact_selection(self):
        new = self._post(
            key='new-custom', scope='custom', cities=['Астана', 'Алматы']
        )
        used = self._post(
            key='used-custom', condition='used', scope='custom',
            cities=['Астана', 'Караганда'],
        )
        self.assertEqual(new.status_code, 200, new.content)
        self.assertEqual(used.status_code, 200, used.content)
        new_req, new_sellers = self._recipients('new-custom')
        used_req, used_sellers = self._recipients('used-custom')
        self.assertEqual(new_req.selected_cities, 'Алматы,Астана')
        self.assertEqual(used_req.selected_cities, 'Астана,Караганда')
        self.assertEqual(new_sellers, {self.new_local.pk, self.new_remote.pk, self.both_remote.pk})
        self.assertEqual(used_sellers, {self.used_remote.pk, self.both_remote.pk})

    def test_no_geo_expansion_when_city_has_no_sellers(self):
        response = self._post(key='new-empty-city', scope='city', city='Костанай')
        self.assertEqual(response.status_code, 200, response.content)
        req, sellers = self._recipients('new-empty-city')
        self.assertEqual(sellers, set())
        self.assertEqual(req.routing_strategy, Request.ROUTING_NO_MATCH)

    def test_custom_requires_city_and_rejects_unknown(self):
        empty = self._post(key='custom-empty', scope='custom')
        unknown = self._post(key='custom-unknown', scope='custom', cities=['Atlantis'])
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(unknown.status_code, 400)
        self.assertIn('selected_cities', empty.json()['fields'])
        self.assertIn('selected_cities', unknown.json()['fields'])

    def test_mode_mismatch_is_not_allowed(self):
        response = self._post(key='conflicting-kind', condition='used', is_dismantler_request='0')
        self.assertEqual(response.status_code, 400)
        self.assertIn('part_condition', response.json()['fields'])

    def test_replay_same_set_in_other_order_and_conflict_new_geo(self):
        first = self._post(key='stable-custom', scope='custom', cities=['Алматы', 'Астана'])
        repeat = self._post(key='stable-custom', scope='custom', cities=['Астана', 'Алматы'])
        conflict = self._post(key='stable-custom', scope='custom', cities=['Алматы'])
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(repeat.status_code, 200, repeat.content)
        self.assertTrue(repeat.json()['replay'])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(Request.objects.filter(idempotency_key='stable-custom').count(), 1)

    def test_manual_brand_and_model_are_accepted(self):
        response = self._post(
            key='manual-vehicle', query='зеркало',
            brand='CustomUnknownBrand', model='X7 Manual',
        )
        self.assertEqual(response.status_code, 200, response.content)
        req = Request.objects.get(idempotency_key='manual-vehicle')
        self.assertEqual(req.brand, 'CustomUnknownBrand')
        self.assertEqual(req.model, 'X7 Manual')

    def test_new_sender_rejects_unverified_and_used_only(self):
        req = Request.objects.create(
            source=Request.SOURCE_HOME_SHORT, city='Алматы', transport_type='car',
            search_scope='kazakhstan', is_dismantler_request=False,
            phone='77019995566', category='Кузов',
        )
        with patch('core.views.send_whatsapp_template') as send:
            for i, seller in enumerate((self.unverified, self.used_local), start=1):
                dispatch = RequestDispatch.objects.create(
                    request=req, seller=seller, wave_number=1,
                    position_number=i, scheduled_at=timezone.now(),
                )
                result = send_single_dispatch(dispatch)
                dispatch.refresh_from_db()
                self.assertEqual(result['reason'], 'new_parts_scope_mismatch')
                self.assertEqual(dispatch.status, RequestDispatch.STATUS_PAUSED)
            send.assert_not_called()
