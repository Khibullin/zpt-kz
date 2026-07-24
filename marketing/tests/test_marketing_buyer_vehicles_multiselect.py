from __future__ import annotations

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.services.buyer_contact_utils import normalize_buyer_text
from core.services.buyer_vehicle_selection import normalize_vehicle_selection
from marketing.models import MarketingAudience
from marketing.services.audiences import GROUP_BUYERS, SUBTYPE_PARTS_REQUESTS, calculate_audience
from marketing.services.buyer_vehicles import (
    build_stats_row_index,
    build_vehicle_selection_from_table_keys,
    compute_selection_totals,
    get_vehicle_stats_rows,
    make_table_row_key,
    validate_table_row_keys,
)
from marketing.services.buyer_vehicles.selection import TableSelectionError
from marketing.tests.test_marketing_audiences import grant_consent, grant_marketing_permission, make_buyer
from marketing.tests.test_marketing_buyer_vehicles import make_request


class BuyerVehicleTableSelectionServiceTests(TestCase):
    def setUp(self):
        self.bmw_x3 = make_buyer()
        self.bmw_7 = make_buyer()
        self.shared = make_buyer()
        make_request(self.bmw_x3, brand='BMW', model='X3')
        make_request(self.bmw_7, brand='BMW', model='7 Series')
        make_request(self.shared, brand='BMW', model='X3')
        make_request(self.shared, brand='BMW', model='7 Series')
        make_request(make_buyer(), brand='Toyota', model='Camry')

    def _row_index(self):
        return build_stats_row_index(get_vehicle_stats_rows())

    def test_single_row_builds_vehicle_selection(self):
        row_index = self._row_index()
        key = make_table_row_key(
            brand_normalized=normalize_buyer_text('BMW'),
            model_normalized=normalize_buyer_text('X3'),
        )
        selection = build_vehicle_selection_from_table_keys([key], row_index)
        self.assertEqual(len(selection), 1)
        self.assertEqual(selection[0]['brand'], 'BMW')
        self.assertEqual(selection[0]['models'], ['X3'])
        self.assertFalse(selection[0]['all_models'])

    def test_multiple_models_same_brand(self):
        row_index = self._row_index()
        keys = [
            make_table_row_key(
                brand_normalized=normalize_buyer_text('BMW'),
                model_normalized=normalize_buyer_text(model),
            )
            for model in ('X3', '7 Series')
        ]
        selection = build_vehicle_selection_from_table_keys(keys, row_index)
        self.assertEqual(len(selection), 1)
        self.assertCountEqual(selection[0]['models'], ['X3', '7 Series'])

    def test_multiple_brands(self):
        row_index = self._row_index()
        keys = [
            make_table_row_key(
                brand_normalized=normalize_buyer_text('BMW'),
                model_normalized=normalize_buyer_text('X3'),
            ),
            make_table_row_key(
                brand_normalized=normalize_buyer_text('Toyota'),
                model_normalized=normalize_buyer_text('Camry'),
            ),
        ]
        selection = build_vehicle_selection_from_table_keys(keys, row_index)
        self.assertEqual(len(selection), 2)
        brands = {entry['brand'] for entry in selection}
        self.assertEqual(brands, {'BMW', 'Toyota'})

    def test_no_cross_product_in_selection(self):
        row_index = self._row_index()
        keys = [
            make_table_row_key(
                brand_normalized=normalize_buyer_text('BMW'),
                model_normalized=normalize_buyer_text('X3'),
            ),
            make_table_row_key(
                brand_normalized=normalize_buyer_text('Toyota'),
                model_normalized=normalize_buyer_text('Camry'),
            ),
        ]
        selection = normalize_vehicle_selection(
            build_vehicle_selection_from_table_keys(keys, row_index),
        )
        bmw = next(entry for entry in selection if entry['brand'] == 'BMW')
        toyota = next(entry for entry in selection if entry['brand'] == 'Toyota')
        self.assertEqual(bmw['models'], ['X3'])
        self.assertEqual(toyota['models'], ['Camry'])

    def test_shared_buyer_deduped_in_totals(self):
        row_index = self._row_index()
        keys = [
            make_table_row_key(
                brand_normalized=normalize_buyer_text('BMW'),
                model_normalized=normalize_buyer_text(model),
            )
            for model in ('X3', '7 Series')
        ]
        totals = compute_selection_totals(keys, row_index)
        self.assertEqual(totals.model_count, 2)
        self.assertEqual(totals.unique_buyers, 3)

    def test_granted_and_live_totals(self):
        granted = make_buyer()
        live = make_buyer()
        test_buyer = make_buyer(is_test_contact=True)
        grant_consent(granted)
        grant_consent(live)
        make_request(granted, brand='Audi', model='A6')
        make_request(live, brand='Audi', model='A6')
        make_request(test_buyer, brand='Audi', model='A6')
        row_index = self._row_index()
        key = make_table_row_key(
            brand_normalized=normalize_buyer_text('Audi'),
            model_normalized=normalize_buyer_text('A6'),
        )
        totals = compute_selection_totals([key], row_index)
        self.assertEqual(totals.granted_count, 2)
        self.assertEqual(totals.live_eligible_count, 2)

    def test_invalid_row_rejected(self):
        row_index = self._row_index()
        with self.assertRaises(TableSelectionError):
            validate_table_row_keys(['invalid:model'], row_index)


class BuyerVehicleTableSelectionViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.url = reverse('marketing:buyer_vehicles')
        make_request(make_buyer(), brand='BMW', model='X3')
        make_request(make_buyer(), brand='BMW', model='7 Series')
        make_request(make_buyer(), brand='Toyota', model='Camry')

    def _key(self, brand: str, model: str) -> str:
        return make_table_row_key(
            brand_normalized=normalize_buyer_text(brand),
            model_normalized=normalize_buyer_text(model),
        )

    def test_selection_totals_json(self):
        keys = [self._key('BMW', 'X3'), self._key('BMW', '7 Series')]
        response = self.client.post(
            self.url,
            {
                'action': 'selection_totals',
                'table_row': keys,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['model_count'], 2)
        self.assertGreaterEqual(payload['unique_buyers'], 2)

    def test_prepare_selection_redirects_to_builder(self):
        keys = [self._key('BMW', 'X3'), self._key('Toyota', 'Camry')]
        response = self.client.post(
            self.url,
            {
                'action': 'prepare_selection',
                'table_row': keys,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('table_select=', response.url)
        self.assertIn('#builder', response.url)

    def test_prepare_selection_prefills_builder(self):
        key = self._key('BMW', 'X3')
        response = self.client.get(f'{self.url}?table_select={key}#builder')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'selection_model__bmw')
        self.assertContains(response, 'checked')

    def test_quick_select_link_same_format(self):
        key = self._key('Toyota', 'Camry')
        response = self.client.get(f'{self.url}?table_select={key}#builder')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'selection_model__toyota')
        self.assertContains(response, 'Camry')

    def test_empty_selection_prepare_blocked(self):
        response = self.client.post(
            self.url,
            {'action': 'prepare_selection'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Выберите хотя бы одну модель')

    def test_invalid_row_rejected_on_prepare(self):
        response = self.client.post(
            self.url,
            {
                'action': 'prepare_selection',
                'table_row': ['invalid:model'],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Недопустимая строка таблицы')

    def test_create_audience_from_table_selection(self):
        buyer = make_buyer()
        grant_consent(buyer)
        make_request(buyer, brand='BMW', model='X3')
        key = self._key('BMW', 'X3')
        self.client.get(f'{self.url}?table_select={key}#builder')
        response = self.client.post(
            self.url,
            {
                'action': 'create_audience',
                'audience_name': 'BMW X3 table audience',
                'selection_brand': [normalize_buyer_text('BMW')],
                f'selection_model__{normalize_buyer_text("BMW")}': ['X3'],
            },
        )
        self.assertEqual(response.status_code, 302)
        audience = MarketingAudience.objects.get(name='BMW X3 table audience')
        self.assertTrue(audience.criteria.get('vehicle_selection'))

    def test_calculate_from_multi_brand_table_selection(self):
        keys = [self._key('BMW', 'X3'), self._key('Toyota', 'Camry')]
        self.client.get(self.url, {'table_select': keys})
        response = self.client.post(
            self.url,
            {
                'action': 'calculate',
                'selection_brand': [
                    normalize_buyer_text('BMW'),
                    normalize_buyer_text('Toyota'),
                ],
                f'selection_model__{normalize_buyer_text("BMW")}': ['X3'],
                f'selection_model__{normalize_buyer_text("Toyota")}': ['Camry'],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Предпросмотр расчёта')

    def test_search_clears_table_selection(self):
        key = self._key('BMW', 'X3')
        self.client.get(f'{self.url}?table_select={key}')
        response = self.client.get(self.url, {'search': 'Toyota'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'value="{key}" checked')


class BuyerVehicleSelectAllTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        make_request(make_buyer(), brand='BMW', model='X3')
        make_request(make_buyer(), brand='Toyota', model='Camry')

    def test_page_contains_select_all_and_clear_link(self):
        response = self.client.get(reverse('marketing:buyer_vehicles'))
        self.assertContains(response, 'table-select-all')
        self.assertContains(response, 'Снять выбор')
        self.assertContains(response, 'prepare-selection-btn')


class BuyerVehicleSelectionAudienceCompatibilityTests(TestCase):
    def test_table_selection_compatible_with_calculate_audience(self):
        buyer = make_buyer()
        grant_consent(buyer)
        make_request(buyer, brand='BMW', model='X3')
        make_request(buyer, brand='Toyota', model='Camry')
        row_index = build_stats_row_index(get_vehicle_stats_rows())
        keys = [
            make_table_row_key(
                brand_normalized=normalize_buyer_text('BMW'),
                model_normalized=normalize_buyer_text('X3'),
            ),
            make_table_row_key(
                brand_normalized=normalize_buyer_text('Toyota'),
                model_normalized=normalize_buyer_text('Camry'),
            ),
        ]
        criteria = {
            'vehicle_selection': build_vehicle_selection_from_table_keys(keys, row_index),
        }
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria=criteria,
        )
        self.assertEqual(result.unique_phones, 1)
        self.assertEqual(result.matched_count, 1)
