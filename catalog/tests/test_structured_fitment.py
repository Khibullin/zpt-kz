from datetime import date
import importlib
import json
from types import SimpleNamespace

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from catalog.models import ProductFitment, Product, Brand, CarModel, Country
from catalog.vehicle_fitment import _product_model_years, _model_engine_codes, ELECTRIC_ENGINE, BODY_FITMENT
from catalog.maintenance_kit_picker import build_kit_picker
from catalog.tests import test_maintenance_kit_picker as legacy_picker_tests


class StructuredFitmentTests(TestCase):
    setUp = legacy_picker_tests.MaintenanceKitPickerTests.setUp
    product = legacy_picker_tests.MaintenanceKitPickerTests.product
    get = legacy_picker_tests.MaintenanceKitPickerTests.get
    def row(self, product, **values):
        product.fitment_reviewed_on = date(2026, 10, 12)
        product.save(update_fields=['fitment_reviewed_on'])
        defaults = dict(car_model=self.model, engine_policy='required', engine_code='SQRE4T15C',
                        year_from=2022, month_from=1, year_to=2025, month_to=12,
                        status='confirmed', source_url='https://example.com/catalogue',
                        verified_on=date(2026, 10, 12))
        return ProductFitment.objects.create(product=product, **(defaults | values))

    def test_structured_rows_override_broad_legacy_text_and_unknown_is_not_wildcard(self):
        self.row(self.air, year_from=2023, year_to=2023)
        self.row(self.oil, year_from=2023, year_to=2023)
        self.assertEqual(self.get().context['years'], [2023])
        self.assertEqual(_model_engine_codes(self.air, self.model, []), {'SQRE4T15C'})
        self.oil.fitments.all().update(engine_policy='unknown', engine_code='')
        self.assertEqual(self.get().context['years'], [])

    def test_disputed_row_and_audited_empty_product_cannot_fall_back_to_prose(self):
        self.row(self.air, status='needs_check')
        self.assertEqual(self.get().context['years'], [])
        self.air.fitments.all().delete()
        self.assertEqual(_model_engine_codes(self.air, self.model, []), set())
        self.assertEqual(self.get().context['years'], [])

    def test_cabin_independence_intersects_real_engine_months(self):
        self.row(self.air, body='T18')
        self.row(self.oil, engine_policy='independent', engine_code='', body='T18', year_from=2023)
        self.assertEqual(self.get().context['years'], [2025, 2024, 2023])
        self.assertEqual(self.get(year='2023').context['engines'], ['SQRE4T15C'])

    def test_disjoint_months_in_same_year_cannot_validate_kit(self):
        self.row(self.air, year_from=2023, month_from=1, year_to=2023, month_to=4)
        self.row(self.oil, year_from=2023, month_from=9, year_to=2023, month_to=12)
        self.assertEqual(self.get().context['years'], [])

    def test_different_generations_or_bodies_do_not_join(self):
        self.row(self.air, generation='I')
        oil_row = self.row(self.oil, generation='II')
        self.assertEqual(self.get().context['years'], [])
        oil_row.generation = 'I'
        oil_row.save()
        self.assertEqual(self.get().context['years'], [2025, 2024, 2023, 2022])

    def test_open_end_stops_at_check_date_and_single_year_is_preserved(self):
        self.row(self.air, year_from=2021, year_to=None, month_to=None, verified_on=date(2024, 5, 1))
        data = _product_model_years(self.air, self.model, [])
        self.assertEqual(data['years'], {2021, 2022, 2023, 2024})
        self.assertNotIn(2024 * 12 + 5, data['engine_months']['SQRE4T15C'])
        self.row(self.oil, year_from=2021, year_to=2021)
        self.assertEqual(_product_model_years(self.oil, self.model, [])['years'], {2021})

    def test_ev_has_explicit_engine_choice_without_fake_ice_code(self):
        self.row(self.air, engine_policy='not_applicable', engine_code='', fuel='Электро')
        data = _product_model_years(self.air, self.model, [])
        self.assertEqual(set(data['engine_years']), {ELECTRIC_ENGINE})

    def test_validation_rejects_incomplete_confirmation_and_reverse_months(self):
        row = self.row(self.air, month_from=11, year_to=2022, month_to=4)
        with self.assertRaises(ValidationError):
            row.full_clean()
        row.year_to = 2025
        row.source_url = ''
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_boundary_year_shows_month_warning_and_product_table(self):
        self.row(self.air, month_from=4)
        self.row(self.oil, month_from=4)
        self.assertContains(self.get(year='2022'), 'Пограничный год')
        response = self.client.get(reverse('product_detail', kwargs={'slug': self.air.slug}), follow=True)
        self.assertContains(response, '04.2022–12.2025')
        self.assertContains(response, 'Каталог')

    def test_both_pickers_include_independent_cabin_for_confirmed_engine(self):
        from django.contrib.auth import get_user_model
        from catalog.models import SellerProfile
        seller = SellerProfile.objects.create(user=get_user_model().objects.create_user(username='structured-owner'), name='AG Parts', slug='ag-parts')
        for p in (self.air, self.oil):
            p.seller_profile=seller;p.save(update_fields=['seller_profile'])
        self.row(self.air)
        self.row(self.oil, engine_policy='independent', engine_code='')
        params={'brand':self.brand.pk,'model':self.model.pk,'year':'2023','engine':'SQRE4T15C'}
        response=self.client.get(reverse('ag_parts_filter_finder'),params)
        self.assertEqual({p.pk for p in response.context['products']},{self.air.pk,self.oil.pk})
        self.assertEqual(len(self.get(year='2023',engine='SQRE4T15C').context['kits']),1)
        for invalid in ({'year':'1900'}, {'engine':'bad-engine'}):
            result=self.client.get(reverse('ag_parts_filter_finder'),params|invalid)
            self.assertEqual(result.context['products'],[])
            self.assertTrue(result.context['invalid_selection'])

    def test_reviewed_prefetch_has_no_per_product_queries(self):
        self.row(self.air);self.row(self.oil)
        from catalog.views_maintenance_kits import _kit_queryset
        kits=list(_kit_queryset().prefetch_related('items__product__car_model', 'items__product__selected_models', 'items__product__fitments__car_model__brand'))
        with self.assertNumQueries(1):
            build_kit_picker(kits, {'brand':str(self.brand.pk),'model':str(self.model.pk)})


class AuditDataTests(SimpleTestCase):
    def test_frozen_data_contains_all_articles_and_exact_dangerous_boundaries(self):
        module=importlib.import_module('catalog.migrations.0073_apply_fitment_audit_20261012')
        data=json.loads(module.DATA_FILE.read_text())
        self.assertEqual(len(data['products']),67)
        by={p['article']:p for p in data['products']}
        self.assertEqual(set(by['F081109111HD']['engine_compatibility'].splitlines()),{'SQRF4J16A','SQRF4J16C','SQRF4J16','SQRF4J20'})
        self.assertEqual({r['model'] for r in by['F081109111HD']['rows']},{'Dashing','X70 Plus','X90 Plus'})
        self.assertTrue(all(r['year_to']<=2014 for r in by['HU71151X']['rows']))
        self.assertTrue(all(r['status']=='needs_check' for r in by['F4J161012030']['rows'] if r['model']=='Tiggo 8 Pro'))
        self.assertNotIn('JLE-3G15TD',by['1056022300']['engine_compatibility'])
        for a in ('1109110XP6EXACHS','1109130U1510','FAE1109160','RF059ZKR','X0390000206'):
            self.assertEqual(by[a]['rows'],[])
        for p in data['products']:
            for row in p['rows']:
                self.assertTrue(row['source_url'],p['article'])
                self.assertTrue(row['verified_on'],p['article'])
                if row['year_to'] is not None:
                    self.assertLessEqual(row['year_from'],row['year_to'])


class AuditMigrationTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from catalog.models import SellerProfile
        self.module=importlib.import_module('catalog.migrations.0073_apply_fitment_audit_20261012')
        self.entry=next(p for p in json.loads(self.module.DATA_FILE.read_text())['products'] if p['article']=='X01-90000014')
        self.seller=SellerProfile.objects.create(user=get_user_model().objects.create_user(username='audit-owner'),name='AG Parts',slug='ag-parts')
        country=Country.objects.create(name='Китай')
        brand=Brand.objects.create(id=self.entry['expected']['brand_id'],name='Li Auto',country=country)
        self.product=Product.objects.create(id=self.entry['id'],article=self.entry['article'],title='Keep title',slug='keep-slug',seller_profile=self.seller,brand=brand,compatibility=self.entry['expected']['compatibility'],engine_compatibility=self.entry['expected']['engine_compatibility'],price=777,stock_qty=3,status='hidden')

    def apply(self):
        self.module.forwards(apps,SimpleNamespace(connection=connection))

    def test_exact_migration_is_idempotent_and_preserves_commercial_fields(self):
        self.apply();self.apply();self.product.refresh_from_db()
        self.assertEqual(self.product.fitments.count(),4)
        self.assertEqual(set(self.product.selected_models.values_list('name',flat=True)),{'L6','L7','L8','L9'})
        self.assertEqual((self.product.price,self.product.stock_qty,self.product.status,self.product.title,self.product.slug),(777,3,'hidden','Keep title','keep-slug'))

    def test_conflicting_seller_edit_aborts_before_mutation(self):
        self.product.compatibility='New owner evidence'
        self.product.save(update_fields=['compatibility'])
        with self.assertRaisesRegex(RuntimeError,'Fitment audit conflict'):
            self.apply()
        self.assertEqual(ProductFitment.objects.count(),0)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.fitment_reviewed_on)
