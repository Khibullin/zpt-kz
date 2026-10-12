from django.test import TestCase
from django.urls import reverse

from catalog.maintenance_kit_picker import build_kit_picker
from catalog.models import Brand, CarModel, Country, MaintenanceKit, MaintenanceKitItem
from catalog.tests.test_maintenance_kits import _make_product
from catalog.views_maintenance_kits import _kit_queryset


class MaintenanceKitPickerTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Kit picker country')
        self.brand = Brand.objects.create(country=country, name='Chery')
        self.model = CarModel.objects.create(brand=self.brand, name='Tiggo 8')
        self.pro = CarModel.objects.create(brand=self.brand, name='Tiggo 8 Pro')
        self.kit = MaintenanceKit.objects.create(
            name='Комплект каскад', slug='cascade-kit', brand=self.brand,
            car_model=self.model, engine='1.5T SQRE4T15B / SQRE4T15C',
            year_from=2018, year_to=2026, is_active=True,
        )
        self.air = self.product('CASCADE-AIR', '2018–2021', '2022–2026')
        self.oil = self.product('CASCADE-OIL', '2020–2021', '2022–2025')
        for product in (self.air, self.oil):
            MaintenanceKitItem.objects.create(kit=self.kit, product=product, quantity=1)

    def product(self, article, years_b, years_c):
        return _make_product(
            article=article, brand=self.brand, car_model=self.model,
            engine_compatibility='SQRE4T15B\nSQRE4T15C',
            compatibility=(
                f'Chery Tiggo 8 SQRE4T15B ({years_b}), SQRE4T15C ({years_c}); '
                'Tiggo 8 Pro SQRF4J16 (2018–2026).'
            ),
        )

    def get(self, **params):
        return self.client.get(reverse('maintenance_kit_list'), {
            'brand': self.brand.pk, 'model': self.model.pk, **params,
        })

    def test_year_choices_intersect_every_component_and_separate_engine_periods(self):
        response = self.get()
        self.assertEqual(response.context['years'], [2025, 2024, 2023, 2022, 2021, 2020])
        self.assertEqual(response.context['engines'], [])
        response = self.get(year='2021')
        self.assertEqual(response.context['engines'], ['SQRE4T15B'])
        response = self.get(year='2023')
        self.assertEqual(response.context['engines'], ['SQRE4T15C'])
        self.assertContains(response, self.kit.name)
        self.assertContains(response, 'Год: 2023')

    def test_zero_stock_is_visible_in_exact_selection(self):
        self.oil.stock_qty = 0
        self.oil.save(update_fields=['stock_qty'])
        response = self.get(year='2023', engine='SQRE4T15C')
        self.assertContains(response, self.kit.name)
        self.assertContains(response, 'Часть позиций нет в наличии')

    def test_one_matching_part_cannot_validate_the_whole_kit(self):
        self.oil.compatibility = ''
        self.oil.save(update_fields=['compatibility'])
        browse = self.get()
        self.assertContains(browse, self.kit.name)
        self.assertTrue(browse.context['year_not_recorded'])
        exact = self.get(year='2023', engine='SQRE4T15C')
        self.assertEqual(exact.context['kits'], [])
        self.assertTrue(exact.context['invalid_selection'])

    def test_different_engine_per_component_cannot_create_false_year_match(self):
        self.oil.engine_compatibility = 'SQRE4T15B'
        self.oil.compatibility = 'Chery Tiggo 8 SQRE4T15B (2022–2025).'
        self.oil.save(update_fields=['engine_compatibility', 'compatibility'])
        self.assertEqual(self.get().context['years'], [])

    def test_foreign_model_and_explicit_engine_exclusion_block_exact_match(self):
        self.oil.car_model = self.pro
        self.oil.save(update_fields=['car_model'])
        self.assertEqual(self.get().context['years'], [])
        self.oil.car_model = self.model
        self.oil.compatibility += ' Не Tiggo 8 SQRE4T15C.'
        self.oil.save(update_fields=['car_model', 'compatibility'])
        self.assertEqual(self.get().context['years'], [2021, 2020])

    def test_invalid_year_or_engine_does_not_silently_broaden_results(self):
        for params in (
            {'year': '2019'}, {'year': 'not-a-year'},
            {'year': '2023', 'engine': 'SQRE4T15B'},
            {'year': '2023', 'engine': 'SQRF4J16'},
        ):
            with self.subTest(params=params):
                response = self.get(**params)
                self.assertEqual(response.context['kits'], [])
                self.assertTrue(response.context['invalid_selection'])

    def test_negative_note_for_another_model_does_not_exclude_this_model(self):
        self.oil.compatibility = (
            'Chery Tiggo 8 SQRE4T15C (2022–2025). '
            'Tiggo 8 Pro SQRF4J16 — не SQRE4T15C.'
        )
        self.oil.save(update_fields=['compatibility'])
        self.assertEqual(self.get(year='2023').context['engines'], ['SQRE4T15C'])

    def test_brand_model_mismatch_resets_dependent_fields(self):
        other = Brand.objects.create(country=self.brand.country, name='Haval')
        other_model = CarModel.objects.create(brand=other, name='Dargo')
        other_kit = MaintenanceKit.objects.create(
            name='Dargo kit', slug='dargo-picker', brand=other,
            car_model=other_model, is_active=True,
        )
        response = self.get(brand=other.pk, year='2023', engine='SQRE4T15C')
        for key in ('selected_model', 'selected_year', 'selected_engine'):
            self.assertEqual(response.context[key], '')
        self.assertEqual(response.context['models'], [other_model])
        self.assertEqual([view.kit.pk for view in response.context['kits']], [other_kit.pk])

    def test_kit_bounds_only_narrow_shared_product_coverage(self):
        self.kit.year_from, self.kit.year_to = 2021, 2023
        self.kit.save(update_fields=['year_from', 'year_to'])
        self.assertEqual(self.get().context['years'], [2023, 2022, 2021])
        self.kit.year_from, self.kit.year_to = None, None
        self.kit.save(update_fields=['year_from', 'year_to'])
        self.assertEqual(self.get().context['years'], [2025, 2024, 2023, 2022, 2021, 2020])

    def test_blank_kit_engine_can_use_common_confirmed_product_engine(self):
        self.kit.engine = ''
        self.kit.save(update_fields=['engine'])
        self.assertEqual(self.get(year='2023').context['engines'], ['SQRE4T15C'])

    def test_shared_product_edit_immediately_changes_both_pickers(self):
        from django.contrib.auth import get_user_model
        from catalog.models import SellerProfile

        seller = SellerProfile.objects.create(
            user=get_user_model().objects.create_user(username='kit-picker'),
            name='AG Parts', slug='ag-parts', phone='+77000000000',
        )
        for product in (self.air, self.oil):
            product.seller_profile = seller
            product.save(update_fields=['seller_profile'])
        params = {'brand': self.brand.pk, 'model': self.model.pk, 'year': '2023', 'engine': 'SQRE4T15C'}
        singles = self.client.get(reverse('ag_parts_filter_finder'), params)
        self.assertEqual({p.pk for p in singles.context['products']}, {self.air.pk, self.oil.pk})
        self.oil.compatibility = 'Chery Tiggo 8 SQRE4T15B (2020–2021).'
        self.oil.save(update_fields=['compatibility'])
        singles = self.client.get(reverse('ag_parts_filter_finder'), params)
        self.assertEqual([p.pk for p in singles.context['products']], [self.air.pk])
        self.assertEqual(self.get(year='2023', engine='SQRE4T15C').context['kits'], [])

    def test_picker_does_not_issue_per_product_queries(self):
        kits = list(_kit_queryset().prefetch_related(
            'items__product__car_model', 'items__product__selected_models',
        ))
        with self.assertNumQueries(1):  # All sibling names in one lookup.
            build_kit_picker(kits, {'brand': str(self.brand.pk), 'model': str(self.model.pk)})
