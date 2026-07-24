from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerVehicle,
    Request,
)
from core.services.buyer_audience_service import build_buyer_audience_queryset
from core.services.buyer_contact_service import rebuild_buyer_contact
from core.services.buyer_contact_utils import normalize_buyer_text
from core.services.buyer_vehicle_selection import (
    build_vehicle_selection_filter_q,
    normalize_vehicle_selection,
)
from marketing.models import MarketingAudience
from marketing.services.audiences import GROUP_BUYERS, SUBTYPE_PARTS_REQUESTS, calculate_audience
from marketing.services.audiences.constants import EXCLUSION_LABELS
from marketing.services.buyer_vehicles import (
    SORT_COUNT_ASC,
    SORT_COUNT_DESC,
    build_audience_criteria,
    get_vehicle_stats_rows,
    suggest_audience_name,
)
from marketing.tests.test_marketing_audiences import grant_consent, grant_marketing_permission, make_buyer, next_phone


def make_vehicle(buyer, *, brand: str, model: str, last_seen_at=None) -> BuyerVehicle:
    seen_at = last_seen_at or timezone.now()
    return BuyerVehicle.objects.create(
        buyer=buyer,
        transport_type='car',
        brand=brand,
        model=model,
        brand_normalized=normalize_buyer_text(brand),
        model_normalized=normalize_buyer_text(model),
        requests_count=1,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )


def make_request(
    buyer,
    *,
    brand: str,
    model: str,
    category: str = '',
    city: str = 'Алматы',
    days_ago: int = 0,
) -> Request:
    created_at = timezone.now() - timedelta(days=days_ago)
    req = Request.objects.create(
        buyer_contact=buyer,
        phone=buyer.phone_normalized,
        transport_type='car',
        brand=brand,
        model=model,
        category=category,
        city=city,
        status='sent',
    )
    Request.objects.filter(pk=req.pk).update(created_at=created_at)
    rebuild_buyer_contact(buyer)
    req.refresh_from_db()
    return req


class BuyerVehicleSelectionLogicTests(TestCase):
    def test_one_brand_all_models(self):
        bmw = make_buyer()
        toyota = make_buyer()
        make_request(bmw, brand='BMW', model='X3')
        make_request(toyota, brand='Toyota', model='Camry')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'BMW', 'all_models': True, 'models': []}]),
            {},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertEqual(phones, {bmw.phone_normalized})

    def test_one_brand_one_model(self):
        buyer = make_buyer()
        make_request(buyer, brand='BMW', model='X3')
        make_request(make_buyer(), brand='BMW', model='X4')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'BMW', 'all_models': False, 'models': ['X3']}]),
            {},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertEqual(phones, {buyer.phone_normalized})

    def test_one_brand_multiple_models(self):
        x3 = make_buyer()
        x4 = make_buyer()
        make_request(x3, brand='BMW', model='X3')
        make_request(x4, brand='BMW', model='X4')
        make_request(make_buyer(), brand='BMW', model='5 Series')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{
                'brand': 'BMW',
                'all_models': False,
                'models': ['X3', 'X4'],
            }]),
            {},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertEqual(phones, {x3.phone_normalized, x4.phone_normalized})

    def test_multiple_brands_all_models(self):
        bmw = make_buyer()
        toyota = make_buyer()
        make_request(bmw, brand='BMW', model='X3')
        make_request(toyota, brand='Toyota', model='Camry')
        make_request(make_buyer(), brand='Audi', model='A6')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([
                {'brand': 'BMW', 'all_models': True, 'models': []},
                {'brand': 'Toyota', 'all_models': True, 'models': []},
            ]),
            {},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertEqual(phones, {bmw.phone_normalized, toyota.phone_normalized})

    def test_multiple_brands_specific_models_no_cross_product(self):
        bmw_x3 = make_buyer()
        toyota_camry = make_buyer()
        make_request(bmw_x3, brand='BMW', model='X3')
        make_request(toyota_camry, brand='Toyota', model='Camry')
        make_request(make_buyer(), brand='BMW', model='Camry')
        make_request(make_buyer(), brand='Toyota', model='X3')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([
                {'brand': 'BMW', 'all_models': False, 'models': ['X3']},
                {'brand': 'Toyota', 'all_models': False, 'models': ['Camry']},
            ]),
            {},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertEqual(phones, {bmw_x3.phone_normalized, toyota_camry.phone_normalized})

    def test_mixed_all_models_plus_specific_model(self):
        bmw_any = make_buyer()
        toyota_camry = make_buyer()
        make_request(bmw_any, brand='BMW', model='5 Series')
        make_request(toyota_camry, brand='Toyota', model='Camry')
        make_request(make_buyer(), brand='Toyota', model='RAV4')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([
                {'brand': 'BMW', 'all_models': True, 'models': []},
                {'brand': 'Toyota', 'all_models': False, 'models': ['Camry']},
            ]),
            {},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertEqual(phones, {bmw_any.phone_normalized, toyota_camry.phone_normalized})

    def test_buyer_with_two_selected_vehicles_deduped(self):
        buyer = make_buyer()
        grant_consent(buyer)
        make_request(buyer, brand='BMW', model='X3')
        make_request(buyer, brand='Toyota', model='Camry')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([
                {'brand': 'BMW', 'all_models': False, 'models': ['X3']},
                {'brand': 'Toyota', 'all_models': False, 'models': ['Camry']},
            ]),
            {},
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria=criteria,
        )
        self.assertEqual(result.unique_phones, 1)
        self.assertEqual(result.matched_count, 1)

    def test_legacy_brands_models_still_work(self):
        buyer = make_buyer()
        make_vehicle(buyer, brand='Toyota', model='Camry')
        phones = set(
            build_buyer_audience_queryset({
                'brands': [normalize_buyer_text('Toyota')],
                'models': [normalize_buyer_text('Camry')],
            }).values_list('phone_normalized', flat=True),
        )
        self.assertEqual(phones, {buyer.phone_normalized})

    def test_city_filter(self):
        almaty = make_buyer(primary_city='Алматы')
        astana = make_buyer(primary_city='Алматы')
        make_request(almaty, brand='Toyota', model='Camry', city='Алматы')
        make_request(astana, brand='Toyota', model='Camry', city='Астана')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'Toyota', 'all_models': True, 'models': []}]),
            {'search_cities': ['Алматы'], 'categories': [], 'category_period': '', 'activity_period': ''},
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria=criteria,
        )
        self.assertEqual(result.matched_count, 1)

    def test_unknown_not_live_eligible(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota', model='Camry')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'Toyota', 'all_models': False, 'models': ['Camry']}]),
            {},
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria=criteria,
        )
        self.assertEqual(result.eligible_count, 0)
        self.assertGreaterEqual(result.unknown_count, 1)

    def test_granted_live_eligible(self):
        buyer = make_buyer()
        grant_consent(buyer)
        make_request(buyer, brand='Toyota', model='Camry')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'Toyota', 'all_models': False, 'models': ['Camry']}]),
            {},
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria=criteria,
        )
        self.assertEqual(result.eligible_count, 1)

    def test_revoked_not_live_eligible(self):
        buyer = make_buyer()
        grant_consent(buyer, status=CONTACT_CONSENT_STATUS_REVOKED)
        make_request(buyer, brand='Toyota', model='Camry')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'Toyota', 'all_models': False, 'models': ['Camry']}]),
            {},
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria=criteria,
        )
        self.assertEqual(result.eligible_count, 0)
        self.assertGreaterEqual(result.revoked_count, 1)

    def test_test_contact_excluded_from_stats(self):
        real = make_buyer(is_test_contact=False)
        test = make_buyer(is_test_contact=True)
        make_vehicle(real, brand='Toyota', model='Camry')
        make_vehicle(test, brand='Toyota', model='Camry')
        rows = get_vehicle_stats_rows()
        camry = next(row for row in rows if row.model == 'Camry')
        self.assertEqual(camry.unique_buyers, 1)


class BuyerVehicleStatsTests(TestCase):
    def test_counts_unique_buyers_not_requests(self):
        buyer = make_buyer()
        make_vehicle(buyer, brand='Toyota', model='Camry')
        rows = get_vehicle_stats_rows()
        camry = next(row for row in rows if row.brand == 'Toyota' and row.model == 'Camry')
        self.assertEqual(camry.unique_buyers, 1)

    def test_sort_ascending_and_descending(self):
        make_vehicle(make_buyer(), brand='Audi', model='A6')
        make_vehicle(make_buyer(), brand='Toyota', model='Camry')
        make_vehicle(make_buyer(), brand='Toyota', model='Camry')
        asc = get_vehicle_stats_rows(sort=SORT_COUNT_ASC)
        desc = get_vehicle_stats_rows(sort=SORT_COUNT_DESC)
        self.assertLessEqual(asc[0].unique_buyers, asc[-1].unique_buyers)
        self.assertGreaterEqual(desc[0].unique_buyers, desc[-1].unique_buyers)


class BuyerVehicleViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.brand_norm = normalize_buyer_text('Toyota')
        self.model = 'Camry'
        self.buyer = make_buyer()
        make_request(self.buyer, brand='Toyota', model='Camry')

    def test_get_does_not_create_audience(self):
        response = self.client.get(reverse('marketing:buyer_vehicles'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MarketingAudience.objects.count(), 0)

    def test_unauthorized_blocked(self):
        self.client.logout()
        response = self.client.get(reverse('marketing:buyer_vehicles'))
        self.assertEqual(response.status_code, 302)

    def test_calculate_preview(self):
        response = self.client.post(
            reverse('marketing:buyer_vehicles'),
            {
                'action': 'calculate',
                'selection_brand': [self.brand_norm],
                f'selection_model__{self.brand_norm}': [self.model],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Предпросмотр расчёта')
        self.assertEqual(MarketingAudience.objects.count(), 0)

    def test_create_audience(self):
        response = self.client.post(
            reverse('marketing:buyer_vehicles'),
            {
                'action': 'create_audience',
                'audience_name': 'Toyota Camry test',
                'selection_brand': [self.brand_norm],
                f'selection_model__{self.brand_norm}': [self.model],
            },
        )
        self.assertEqual(response.status_code, 302)
        audience = MarketingAudience.objects.get()
        self.assertEqual(audience.contact_subtype, SUBTYPE_PARTS_REQUESTS)
        self.assertTrue(audience.criteria.get('vehicle_selection'))

    def test_quick_select_prefills_form(self):
        response = self.client.get(
            reverse('marketing:buyer_vehicles'),
            {
                'preselect_brand': self.brand_norm,
                'preselect_model': normalize_buyer_text(self.model),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'checked')

    def test_invalid_brand_rejected(self):
        response = self.client.post(
            reverse('marketing:buyer_vehicles'),
            {
                'action': 'calculate',
                'selection_brand': ['invalid-brand'],
                f'selection_all_models__invalid-brand': '1',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Недопустимая марка')

    def test_suggested_name(self):
        name = suggest_audience_name(normalize_vehicle_selection([
            {'brand': 'Toyota', 'all_models': False, 'models': ['Camry']},
        ]))
        self.assertIn('Toyota', name)


class BuyerVehicleFilterQTests(TestCase):
    def test_filter_q_preserves_brand_model_pair(self):
        q = build_vehicle_selection_filter_q(normalize_vehicle_selection([
            {'brand': 'BMW', 'all_models': False, 'models': ['X3']},
            {'brand': 'Toyota', 'all_models': False, 'models': ['Camry']},
        ]))
        self.assertIn('vehicles__brand_normalized', str(q))
        self.assertIn('vehicles__model_normalized', str(q))


class BuyerVehicleLinkedSemanticsRegressionTests(TestCase):
    def test_a_bmw_old_toyota_recent_filter_bmw_last90_no_match(self):
        buyer = make_buyer()
        make_request(buyer, brand='BMW', model='X3', days_ago=365)
        make_request(buyer, brand='Toyota', model='Camry', days_ago=7)
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'BMW', 'all_models': False, 'models': ['X3']}]),
            {'activity_period': 'last_90_days'},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertNotIn(buyer.phone_normalized, phones)

    def test_b_bmw_recent_toyota_old_filter_bmw_last90_match(self):
        buyer = make_buyer()
        make_request(buyer, brand='BMW', model='X3', days_ago=7)
        make_request(buyer, brand='Toyota', model='Camry', days_ago=365)
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'BMW', 'all_models': False, 'models': ['X3']}]),
            {'activity_period': 'last_90_days'},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertIn(buyer.phone_normalized, phones)

    def test_c_bmw_engine_toyota_brakes_filter_bmw_brakes_no_match(self):
        buyer = make_buyer()
        make_request(buyer, brand='BMW', model='X3', category='Двигатель')
        make_request(buyer, brand='Toyota', model='Camry', category='Тормозные колодки')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'BMW', 'all_models': False, 'models': ['X3']}]),
            {'categories': ['Тормозные колодки']},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertNotIn(buyer.phone_normalized, phones)

    def test_d_bmw_engine_filter_bmw_engine_match(self):
        buyer = make_buyer()
        make_request(buyer, brand='BMW', model='X3', category='Двигатель')
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'BMW', 'all_models': False, 'models': ['X3']}]),
            {'categories': ['Двигатель']},
        )
        phones = set(build_buyer_audience_queryset(criteria).values_list('phone_normalized', flat=True))
        self.assertIn(buyer.phone_normalized, phones)

    def test_e_toyota_camry_last_date_not_replaced_by_other_vehicle(self):
        buyer = make_buyer()
        camry_date = timezone.now() - timedelta(days=30)
        make_request(buyer, brand='Toyota', model='Camry', days_ago=30)
        make_request(buyer, brand='BMW', model='X3', days_ago=1)
        rows = get_vehicle_stats_rows()
        camry = next(row for row in rows if row.brand == 'Toyota' and row.model == 'Camry')
        self.assertIsNotNone(camry.last_request_at)
        self.assertAlmostEqual(
            camry.last_request_at.timestamp(),
            camry_date.timestamp(),
            delta=5,
        )

    def test_f_one_buyer_multiple_matching_requests_stays_one_recipient(self):
        buyer = make_buyer()
        grant_consent(buyer)
        make_request(buyer, brand='BMW', model='X3', days_ago=10)
        make_request(buyer, brand='BMW', model='X3', days_ago=5)
        criteria = build_audience_criteria(
            normalize_vehicle_selection([{'brand': 'BMW', 'all_models': False, 'models': ['X3']}]),
            {'activity_period': 'last_90_days'},
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria=criteria,
        )
        self.assertEqual(result.unique_phones, 1)
        self.assertEqual(result.matched_count, 1)
