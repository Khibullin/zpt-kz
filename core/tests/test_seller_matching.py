"""Regression tests for seller matching without M2M row multiplication."""
from __future__ import annotations

from django.db.models import Q
from django.test import TestCase

from core.models import (
    Brand,
    BroadcastSettings,
    CarModel,
    Country,
    PartCategory,
    Request,
    Seller,
)
from core.tests.test_request_dispatch_waves import _ensure_broadcast_settings
from core.views import _find_matching_sellers


def _find_matching_sellers_legacy(req):
    """Reference copy of the pre-Exists matching algorithm (JOIN + DISTINCT)."""
    settings = BroadcastSettings.load()
    base = Seller.objects.filter(
        is_active=True,
        is_paused=False,
        transport_type=req.transport_type,
    )
    if settings.emergency_stop:
        base_qs = Seller.objects.none()
    elif settings.mode == 'off':
        base_qs = Seller.objects.none()
    elif settings.mode == 'test':
        base_qs = base.filter(is_test_seller=True).distinct()
    elif settings.mode == 'live':
        base_qs = base.filter(receive_requests=True).distinct()
    else:
        base_qs = Seller.objects.none()

    if req.category:
        base_qs = base_qs.filter(
            Q(all_categories=True) |
            Q(category=req.category) |
            Q(selected_categories__name=req.category)
        ).distinct()

    search_scope = req.search_scope or 'city'
    selected_cities = []
    if req.selected_cities:
        selected_cities = [
            city.strip()
            for city in req.selected_cities.split(',')
            if city.strip()
        ]

    strategies = [
        {'country': True, 'brand': True, 'model': True},
        {'country': True, 'brand': True, 'model': False},
        {'country': True, 'brand': False, 'model': False},
    ]

    def apply_country(qs):
        if not req.country:
            return qs
        return qs.filter(
            Q(all_countries=True) |
            Q(country_fk__name=req.country) |
            Q(selected_countries__name=req.country)
        ).distinct()

    def apply_brand(qs):
        if not req.brand:
            return qs
        return qs.filter(
            Q(all_brands=True) |
            Q(brand=req.brand) |
            Q(brand_fk__name=req.brand) |
            Q(selected_brands__name=req.brand)
        ).distinct()

    def apply_model(qs):
        if not req.model:
            return qs
        return qs.filter(
            Q(all_brands=True) |
            Q(all_models=True) |
            Q(model=req.model) |
            Q(model_fk__name=req.model) |
            Q(selected_models__name=req.model)
        ).distinct()

    for strategy in strategies:
        qs = base_qs
        if search_scope == 'city' and req.city:
            qs = qs.filter(city=req.city)
        elif search_scope == 'custom' and selected_cities:
            qs = qs.filter(city__in=selected_cities)
        if strategy['country']:
            qs = apply_country(qs)
        if strategy['brand']:
            qs = apply_brand(qs)
        if strategy['model']:
            qs = apply_model(qs)
        qs = qs.order_by('dispatch_priority', 'id').distinct()
        if qs.exists():
            return qs, 'matched'

    if search_scope in ['city', 'custom']:
        for strategy in strategies:
            qs = base_qs
            if strategy['country']:
                qs = apply_country(qs)
            if strategy['brand']:
                qs = apply_brand(qs)
            if strategy['model']:
                qs = apply_model(qs)
            qs = qs.order_by('dispatch_priority', 'id').distinct()
            if qs.exists():
                return qs, 'fallback_kazakhstan'

    return Seller.objects.none(), 'no_match'


def _make_request(**kwargs) -> Request:
    defaults = {
        'transport_type': 'car',
        'country': 'Китай',
        'brand': 'Dongfeng',
        'model': '580',
        'category': 'Двигатель',
        'city': 'Алматы',
        'search_scope': 'kazakhstan',
        'phone': '77001112233',
        'status': 'new',
    }
    defaults.update(kwargs)
    return Request.objects.create(**defaults)


class SellerMatchingExistsTests(TestCase):
    def setUp(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            emergency_stop=False,
        )
        self.country_cn = Country.objects.create(name='Китай')
        self.country_jp = Country.objects.create(name='Япония')
        self.brand_df = Brand.objects.create(
            name='Dongfeng',
            country=self.country_cn,
            transport_type='car',
        )
        self.brand_ty = Brand.objects.create(
            name='Toyota',
            country=self.country_jp,
            transport_type='car',
        )
        self.model_580 = CarModel.objects.create(
            name='580',
            brand=self.brand_df,
            transport_type='car',
        )
        self.model_camry = CarModel.objects.create(
            name='Camry',
            brand=self.brand_ty,
            transport_type='car',
        )
        self.cat_engine = PartCategory.objects.create(name='Двигатель')
        self.cat_brakes = PartCategory.objects.create(name='Тормоза')
        self.extra_cats = [
            PartCategory.objects.create(name=f'Cat{i}') for i in range(5)
        ]
        self.extra_countries = [
            Country.objects.create(name=f'Country{i}')
            for i in range(5)
        ]
        self.extra_brands = [
            Brand.objects.create(
                name=f'Brand{i}',
                country=self.country_cn,
                transport_type='car',
            )
            for i in range(5)
        ]
        self.extra_models = [
            CarModel.objects.create(
                name=f'Model{i}',
                brand=self.brand_df,
                transport_type='car',
            )
            for i in range(5)
        ]

    def test_all_categories_true(self):
        seller = Seller.objects.create(
            name='All cats',
            whatsapp='77010000001',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=10,
        )
        req = _make_request()
        sellers, strategy = _find_matching_sellers(req)
        ids = [s.id for s in sellers]
        self.assertEqual(strategy, 'matched')
        self.assertEqual(ids, [seller.id])

    def test_legacy_category_field(self):
        seller = Seller.objects.create(
            name='Legacy cat',
            whatsapp='77010000002',
            transport_type='car',
            receive_requests=True,
            category='Двигатель',
            all_countries=True,
            all_brands=True,
        )
        req = _make_request()
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual([s.id for s in sellers], [seller.id])

    def test_selected_categories_m2m(self):
        seller = Seller.objects.create(
            name='M2M cat',
            whatsapp='77010000003',
            transport_type='car',
            receive_requests=True,
            all_countries=True,
            all_brands=True,
        )
        seller.selected_categories.add(self.cat_engine)
        req = _make_request()
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual([s.id for s in sellers], [seller.id])

    def test_country_fk_and_selected_countries(self):
        via_fk = Seller.objects.create(
            name='FK country',
            whatsapp='77010000004',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            country_fk=self.country_cn,
            all_brands=True,
            dispatch_priority=1,
        )
        via_m2m = Seller.objects.create(
            name='M2M country',
            whatsapp='77010000005',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_brands=True,
            dispatch_priority=2,
        )
        via_m2m.selected_countries.add(self.country_cn)
        all_countries = Seller.objects.create(
            name='All countries',
            whatsapp='77010000006',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=3,
        )
        req = _make_request()
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual(
            [s.id for s in sellers],
            [via_fk.id, via_m2m.id, all_countries.id],
        )

    def test_brand_paths(self):
        legacy = Seller.objects.create(
            name='Legacy brand',
            whatsapp='77010000007',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            brand='Dongfeng',
            all_models=True,
            dispatch_priority=1,
        )
        via_fk = Seller.objects.create(
            name='FK brand',
            whatsapp='77010000008',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            brand_fk=self.brand_df,
            all_models=True,
            dispatch_priority=2,
        )
        via_m2m = Seller.objects.create(
            name='M2M brand',
            whatsapp='77010000009',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_models=True,
            dispatch_priority=3,
        )
        via_m2m.selected_brands.add(self.brand_df)
        all_brands = Seller.objects.create(
            name='All brands',
            whatsapp='77010000010',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=4,
        )
        req = _make_request()
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual(
            [s.id for s in sellers],
            [legacy.id, via_fk.id, via_m2m.id, all_brands.id],
        )

    def test_model_paths(self):
        legacy = Seller.objects.create(
            name='Legacy model',
            whatsapp='77010000011',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            brand='Dongfeng',
            model='580',
            dispatch_priority=1,
        )
        via_fk = Seller.objects.create(
            name='FK model',
            whatsapp='77010000012',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            brand_fk=self.brand_df,
            model_fk=self.model_580,
            dispatch_priority=2,
        )
        via_m2m = Seller.objects.create(
            name='M2M model',
            whatsapp='77010000013',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            brand='Dongfeng',
            dispatch_priority=3,
        )
        via_m2m.selected_models.add(self.model_580)
        all_models = Seller.objects.create(
            name='All models',
            whatsapp='77010000014',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            brand='Dongfeng',
            all_models=True,
            dispatch_priority=4,
        )
        req = _make_request()
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual(
            [s.id for s in sellers],
            [legacy.id, via_fk.id, via_m2m.id, all_models.id],
        )

    def test_city_scope_and_fallback_kazakhstan(self):
        local = Seller.objects.create(
            name='Local',
            whatsapp='77010000015',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=1,
        )
        other_city = Seller.objects.create(
            name='Astana',
            whatsapp='77010000016',
            transport_type='car',
            city='Астана',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=2,
        )
        req = _make_request(search_scope='city', city='Алматы')
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual([s.id for s in sellers], [local.id])

        local.is_active = False
        local.save(update_fields=['is_active'])
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'fallback_kazakhstan')
        self.assertEqual([s.id for s in sellers], [other_city.id])

    def test_custom_cities_scope(self):
        almaty = Seller.objects.create(
            name='Almaty custom',
            whatsapp='77010000017',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=1,
        )
        Seller.objects.create(
            name='Shymkent',
            whatsapp='77010000018',
            transport_type='car',
            city='Шымкент',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=2,
        )
        req = _make_request(
            search_scope='custom',
            selected_cities='Алматы,Астана',
            city='Алматы',
        )
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual([s.id for s in sellers], [almaty.id])

    def test_no_duplicate_with_many_m2m_rows(self):
        seller = Seller.objects.create(
            name='Multi M2M',
            whatsapp='77010000019',
            transport_type='car',
            receive_requests=True,
            dispatch_priority=5,
        )
        seller.selected_categories.add(self.cat_engine, *self.extra_cats)
        seller.selected_countries.add(self.country_cn, *self.extra_countries)
        seller.selected_brands.add(self.brand_df, *self.extra_brands)
        seller.selected_models.add(self.model_580, *self.extra_models)

        req = _make_request()
        sellers, strategy = _find_matching_sellers(req)
        ids = [s.id for s in list(sellers)]
        self.assertEqual(strategy, 'matched')
        self.assertEqual(ids, [seller.id])
        self.assertEqual(len(ids), len(set(ids)))

        sql = str(sellers.query).upper()
        self.assertNotIn('DISTINCT', sql)
        self.assertIn('EXISTS', sql)

    def test_dispatch_priority_order(self):
        late = Seller.objects.create(
            name='Late',
            whatsapp='77010000020',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=50,
        )
        early = Seller.objects.create(
            name='Early',
            whatsapp='77010000021',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=1,
        )
        mid = Seller.objects.create(
            name='Mid',
            whatsapp='77010000022',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=10,
        )
        req = _make_request()
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual([s.id for s in sellers], [early.id, mid.id, late.id])

    def test_strategy_relax_to_country_brand(self):
        seller = Seller.objects.create(
            name='Brand only match',
            whatsapp='77010000023',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            brand='Dongfeng',
            model='OtherModel',
        )
        req = _make_request(model='580')
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual([s.id for s in sellers], [seller.id])

    def test_new_matches_legacy_algorithm(self):
        cases = []

        s1 = Seller.objects.create(
            name='Eq all flags',
            whatsapp='77010000101',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            dispatch_priority=1,
        )
        cases.append(s1)

        s2 = Seller.objects.create(
            name='Eq m2m heavy',
            whatsapp='77010000102',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
            dispatch_priority=2,
        )
        s2.selected_categories.add(self.cat_engine, *self.extra_cats[:2])
        s2.selected_countries.add(self.country_cn, *self.extra_countries[:2])
        s2.selected_brands.add(self.brand_df, *self.extra_brands[:2])
        s2.selected_models.add(self.model_580, *self.extra_models[:2])
        cases.append(s2)

        s3 = Seller.objects.create(
            name='Eq legacy fields',
            whatsapp='77010000103',
            transport_type='car',
            city='Астана',
            receive_requests=True,
            category='Двигатель',
            brand='Dongfeng',
            model='580',
            country_fk=self.country_cn,
            dispatch_priority=3,
        )
        cases.append(s3)

        s4 = Seller.objects.create(
            name='Eq fk only',
            whatsapp='77010000104',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            country_fk=self.country_cn,
            brand_fk=self.brand_df,
            model_fk=self.model_580,
            dispatch_priority=4,
        )
        cases.append(s4)

        Seller.objects.create(
            name='Non match japan',
            whatsapp='77010000105',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            country_fk=self.country_jp,
            brand_fk=self.brand_ty,
            model_fk=self.model_camry,
            dispatch_priority=5,
        )

        requests = [
            _make_request(search_scope='kazakhstan'),
            _make_request(search_scope='city', city='Алматы'),
            _make_request(
                search_scope='custom',
                selected_cities='Алматы,Астана',
                city='Алматы',
            ),
            _make_request(search_scope='city', city='Караганда'),
            _make_request(brand='Toyota', model='Camry', country='Япония'),
        ]

        for req in requests:
            with self.subTest(request_id=req.id, scope=req.search_scope):
                new_qs, new_strategy = _find_matching_sellers(req)
                old_qs, old_strategy = _find_matching_sellers_legacy(req)
                self.assertEqual(new_strategy, old_strategy)
                self.assertEqual(
                    [s.id for s in new_qs],
                    [s.id for s in old_qs],
                )

    def test_broadcast_modes(self):
        live = Seller.objects.create(
            name='Live seller',
            whatsapp='77010000110',
            transport_type='car',
            receive_requests=True,
            all_categories=True,
            all_countries=True,
            all_brands=True,
        )
        test = Seller.objects.create(
            name='Test seller',
            whatsapp='77010000111',
            transport_type='car',
            is_test_seller=True,
            receive_requests=False,
            all_categories=True,
            all_countries=True,
            all_brands=True,
        )
        req = _make_request()

        _ensure_broadcast_settings(mode=BroadcastSettings.MODE_TEST)
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertEqual([s.id for s in sellers], [test.id])

        _ensure_broadcast_settings(mode=BroadcastSettings.MODE_OFF)
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'no_match')
        self.assertEqual(list(sellers), [])

        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            emergency_stop=True,
        )
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'no_match')
        self.assertEqual(list(sellers), [])

        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            emergency_stop=False,
        )
        sellers, strategy = _find_matching_sellers(req)
        self.assertEqual(strategy, 'matched')
        self.assertIn(live.id, [s.id for s in sellers])
