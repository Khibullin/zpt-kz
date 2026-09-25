from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from catalog.ag_parts_fitment_audit import (
    apply_fitment_plans,
    load_batch,
    plan_fitment_batch,
)
from catalog.models import Brand, CarModel, Country, Product, ProductPriceTier
from catalog.product_quality import detect_internal_research_text


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'FIT-B4',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch04Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B4')
        self.jetour = Brand.objects.create(country=country, name='Jetour')
        self.chery = Brand.objects.create(country=country, name='Chery')
        self.exeed = Brand.objects.create(country=country, name='Exeed')
        self.jaecoo = Brand.objects.create(country=country, name='Jaecoo')
        self.dashing = CarModel.objects.create(brand=self.jetour, name='Dashing')
        self.x70 = CarModel.objects.create(brand=self.jetour, name='X70')
        self.x90 = CarModel.objects.create(brand=self.jetour, name='X90')
        self.tiggo8 = CarModel.objects.create(brand=self.chery, name='Tiggo 8')
        self.tiggo8_pro_max = CarModel.objects.create(brand=self.chery, name='Tiggo 8 Pro Max')
        self.tiggo9 = CarModel.objects.create(brand=self.chery, name='Tiggo 9')
        self.rx = CarModel.objects.create(brand=self.exeed, name='RX')
        self.j8 = CarModel.objects.create(brand=self.jaecoo, name='J8')

        self.f081 = _product(
            article='F081109111HD',
            title='Воздушный фильтр Jetour X70 / Dashing / X90 Plus — F081109111HD',
            slug='jetour-x70-dashing-x90-plus-f081109111hd',
            brand=self.jetour,
            car_model=self.x70,
            price=3300,
            stock_qty=5,
            compatibility='Jetour X70, Dashing, X90 Plus; 2022–2025',
            oem_cross_references='FA-0929JM',
            description='Применимость: Jetour X70, Dashing, X90 Plus; 2022–2025.',
        )
        ProductPriceTier.objects.create(product=self.f081, min_qty=1, price=3300)

        self.air151 = _product(
            article='151000151AA',
            title='Воздушный фильтр 151000151AA',
            slug='151000151aa',
            brand=self.chery,
            price=2950,
            stock_qty=7,
            compatibility=(
                'Chery Tiggo 8 — 2021–2023; Tiggo 8 Pro Max — с 2022 года; '
                'Tiggo 9 — с 2023 года; EXEED RX — с 2023 года; '
                'JAECOO J8 — с 2023 года; KAIYI X7 Kunlun — с 2023 года'
            ),
            description='Применимость: Chery Tiggo 8; Tiggo 9; EXEED RX; JAECOO J8.',
        )
        self.air151.selected_models.add(
            self.tiggo8, self.tiggo8_pro_max, self.tiggo9, self.rx, self.j8,
        )

    def test_batch_04_has_no_research_notes(self):
        spec = load_batch('04')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_04_f081_and_strip_151(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('04')), apply=True)

        self.f081.refresh_from_db()
        self.assertIn('X70 Plus', self.f081.title)
        self.assertIn('Dashing', self.f081.title)
        self.assertIn('SQRF4J16A', self.f081.engine_compatibility)
        self.assertIn('не подтверждаем', self.f081.compatibility)
        self.assertNotIn('2022–2025', self.f081.compatibility)
        self.assertNotIn('FA-0929JM', self.f081.oem_cross_references)
        names = set(self.f081.selected_models.values_list('name', flat=True))
        self.assertEqual(names, {'Dashing', 'X70', 'X90'})
        self.assertEqual(self.f081.car_model, self.dashing)
        self.assertEqual(self.f081.price, 3300)
        self.assertEqual(self.f081.stock_qty, 5)
        self.assertEqual(self.f081.slug, 'jetour-x70-dashing-x90-plus-f081109111hd')

        old_page = self.client.get(f'/{self.f081.slug}/')
        self.assertEqual(old_page.status_code, 301)
        self.assertEqual(old_page['Location'], '/vozdushnyi-filtr-f081109111hd/')
        page = self.client.get('/vozdushnyi-filtr-f081109111hd/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'X70 Plus')
        self.assertContains(page, 'SQRF4J16A')
        self.assertContains(page, '<li>Dashing</li>')

        self.air151.refresh_from_db()
        self.assertEqual(self.air151.price, 2950)
        self.assertFalse(self.air151.selected_models.exists())
        self.assertNotIn('Tiggo 9', self.air151.compatibility)
        self.assertNotIn('EXEED RX', self.air151.compatibility)
        self.assertNotIn('JAECOO J8', self.air151.compatibility)
        self.assertIn('151000079AA', self.air151.description)
        air_page = self.client.get('/151000151aa/')
        self.assertEqual(air_page.status_code, 200)
        self.assertNotContains(air_page, 'Tiggo 9')
        self.assertNotContains(air_page, 'JAECOO J8')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '04', stdout=out)
        self.f081.refresh_from_db()
        self.assertIn('Jetour X70 / Dashing', self.f081.title)
        self.assertIn('dry-run', out.getvalue())
