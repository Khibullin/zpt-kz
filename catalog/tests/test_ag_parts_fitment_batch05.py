from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from catalog.ag_parts_fitment_audit import (
    apply_fitment_plans,
    load_batch,
    plan_fitment_batch,
)
from catalog.fitment_slug_redirects import FITMENT_SLUG_REDIRECTS
from catalog.models import Brand, CarModel, Country, Product, ProductPriceTier
from catalog.product_quality import detect_internal_research_text


OLD_F081_SLUG = 'jetour-x70-dashing-x90-plus-f081109111hd'
NEW_F081_SLUG = 'vozdushnyi-filtr-f081109111hd'


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'FIT-B5',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch05Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B5')
        self.jetour = Brand.objects.create(country=country, name='Jetour')
        self.gwm = Brand.objects.create(country=country, name='Great Wall')
        self.changan = Brand.objects.create(country=country, name='Changan')
        self.geely = Brand.objects.create(country=country, name='Geely')
        self.dashing = CarModel.objects.create(brand=self.jetour, name='Dashing')
        self.x70 = CarModel.objects.create(brand=self.jetour, name='X70')
        self.x70_plus = CarModel.objects.create(brand=self.jetour, name='X70 Plus')
        self.x90 = CarModel.objects.create(brand=self.jetour, name='X90')
        self.wingle7 = CarModel.objects.create(brand=self.gwm, name='Wingle 7')
        self.cs75 = CarModel.objects.create(brand=self.changan, name='CS75 Plus')
        self.unik = CarModel.objects.create(brand=self.changan, name='UNI-K')
        self.emgrand = CarModel.objects.create(brand=self.geely, name='Emgrand')

        self.f081 = _product(
            article='F081109111HD',
            title='Воздушный фильтр Jetour Dashing / X70 Plus — F081109111HD',
            slug=OLD_F081_SLUG,
            brand=self.jetour,
            car_model=self.dashing,
            price=3300,
            stock_qty=5,
            compatibility='Jetour Dashing 1.6 SQRF4J16A. Голый X70 этим артикулом не подтверждаем.',
        )
        self.f081.selected_models.add(self.dashing, self.x70, self.x90)
        ProductPriceTier.objects.create(product=self.f081, min_qty=1, price=3300)

        self.wingle_air = _product(
            article='1109110XP64XA',
            title='Воздушный фильтр 1109110XP64XA',
            slug='1109110xp64xa',
            brand=self.gwm,
            price=2640,
            stock_qty=8,
            compatibility='Great Wall Wingle 5 — 2011–2023; Great Wall Wingle 7 — с 2018 года',
            oem_cross_references='1109110P64',
        )

        self.spark = _product(
            article='D20T0120700',
            title='Свеча зажигания Changan CS75 Plus — D20T0120700',
            slug='changan-cs75-plus-d20t0120700-changan-cs75-plus',
            brand=self.changan,
            car_model=self.cs75,
            price=2800,
            stock_qty=20,
            compatibility='Changan CS75 Plus, UNI-K — 2.0T.',
            engine_compatibility='2.0T',
            oem_cross_references='D20T0120700\nD20T012-0700',
        )
        self.spark.selected_models.add(self.cs75, self.unik)

        self.geely_air = _product(
            article='1064000180',
            title='Воздушный фильтр 1064000180',
            slug='1064000180',
            brand=self.geely,
            price=1800,
            stock_qty=15,
            compatibility='Geely Emgrand EC7, Emgrand EC7RV, GC7, FC, SL — 1.5–1.8 л',
            oem_cross_references='71-01286-SX',
        )
        self.geely_air.selected_models.add(self.emgrand)

    def test_batch_05_has_no_research_notes(self):
        spec = load_batch('05')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_f081_slug_redirect_is_registered(self):
        self.assertEqual(FITMENT_SLUG_REDIRECTS[OLD_F081_SLUG], NEW_F081_SLUG)

    def test_apply_batch_05_slug_engines_and_strip(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('05')), apply=True)

        self.f081.refresh_from_db()
        self.assertEqual(self.f081.slug, NEW_F081_SLUG)
        self.assertEqual(self.f081.get_absolute_url(), f'/{NEW_F081_SLUG}/')
        self.assertEqual(self.f081.price, 3300)
        self.assertEqual(self.f081.stock_qty, 5)
        names = set(self.f081.selected_models.values_list('name', flat=True))
        self.assertEqual(names, {'Dashing', 'X70 Plus', 'X90'})
        self.assertNotIn('X70', names)

        old = self.client.get(f'/{OLD_F081_SLUG}/')
        self.assertEqual(old.status_code, 301)
        self.assertEqual(old['Location'], f'/{NEW_F081_SLUG}/')
        new = self.client.get(f'/{NEW_F081_SLUG}/')
        self.assertEqual(new.status_code, 200)
        self.assertContains(new, 'X70 Plus')
        self.assertContains(new, f'https://zpt.kz/{NEW_F081_SLUG}/')
        self.assertContains(new, '<li>X70 Plus</li>')
        self.assertNotContains(new, '<li>X70</li>')

        search = self.client.get('/', {'q': 'F081109111HD'})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, f'/{NEW_F081_SLUG}/')
        self.assertNotContains(search, f'/{OLD_F081_SLUG}/')

        self.wingle_air.refresh_from_db()
        self.assertEqual(self.wingle_air.price, 2640)
        self.assertIn('GW4D20', self.wingle_air.engine_compatibility)
        self.assertIn('Wingle 7', self.wingle_air.title)
        self.assertEqual(
            set(self.wingle_air.selected_models.values_list('name', flat=True)),
            {'Wingle 7'},
        )
        wingle_page = self.client.get('/1109110xp64xa/')
        self.assertEqual(wingle_page.status_code, 200)
        self.assertContains(wingle_page, 'GW4D20D')

        self.spark.refresh_from_db()
        self.assertEqual(self.spark.price, 2800)
        self.assertIn('JL486ZQ4', self.spark.engine_compatibility)
        self.assertNotIn('UNI-K', list(self.spark.selected_models.values_list('name', flat=True)))
        spark_page = self.client.get('/changan-cs75-plus-d20t0120700-changan-cs75-plus/')
        self.assertEqual(spark_page.status_code, 200)
        self.assertContains(spark_page, 'JL486ZQ4')
        self.assertContains(spark_page, 'не подтверждаем')
        self.assertNotContains(spark_page, '<li>UNI-K</li>')

        self.geely_air.refresh_from_db()
        self.assertEqual(self.geely_air.price, 1800)
        self.assertFalse(self.geely_air.selected_models.exists())
        self.assertNotIn('EC7', self.geely_air.compatibility)
        self.assertNotIn('71-01286-SX', self.geely_air.oem_cross_references)
        geely_page = self.client.get('/1064000180/')
        self.assertEqual(geely_page.status_code, 200)
        self.assertNotContains(geely_page, 'Emgrand EC7')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '05', stdout=out)
        self.f081.refresh_from_db()
        self.assertEqual(self.f081.slug, OLD_F081_SLUG)
        self.assertIn('dry-run', out.getvalue())
