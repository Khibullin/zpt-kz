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


OLD_XP24_SLUG = 'haval-h6'
NEW_XP24_SLUG = 'salonnyi-filtr-8104400xp24ba'


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'FIT-B6',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch06Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B6')
        self.li = Brand.objects.create(country=country, name='Li Auto')
        self.changan = Brand.objects.create(country=country, name='Changan')
        self.gwm = Brand.objects.create(country=country, name='Great Wall')
        self.l6 = CarModel.objects.create(brand=self.li, name='L6')
        self.l7 = CarModel.objects.create(brand=self.li, name='L7')
        self.l8 = CarModel.objects.create(brand=self.li, name='L8')
        self.l9 = CarModel.objects.create(brand=self.li, name='L9')
        self.cs35 = CarModel.objects.create(brand=self.changan, name='CS35')
        self.cs35_plus = CarModel.objects.create(brand=self.changan, name='CS35 Plus')
        self.eado_plus = CarModel.objects.create(brand=self.changan, name='Eado Plus')
        self.wingle7 = CarModel.objects.create(brand=self.gwm, name='Wingle 7')
        self.wingle6 = CarModel.objects.create(brand=self.gwm, name='Wingle 6')

        self.x01 = _product(
            article='X01-90000014',
            title='Воздушный фильтр Li Auto L7 — X01-90000014',
            slug='li-auto-l7-x01-90000014-li-auto-l7',
            brand=self.li,
            car_model=self.l7,
            price=985,
            stock_qty=28,
            compatibility='Li Auto L6, L7, L8, L9. Это воздушный фильтр двигателя, не салонный.',
            oem_cross_references='X01-90000014\nX0190000014\nX01-29150063',
            description='OEM: X01-90000014; X0190000014; X01-29150063. Li Auto L6, L7, L8, L9.',
        )
        self.x01.selected_models.add(self.l6, self.l7, self.l8, self.l9)
        ProductPriceTier.objects.create(product=self.x01, min_qty=1, price=985)

        self.s101 = _product(
            article='S1010140400',
            title='Воздушный фильтр Changan CS35 — S1010140400',
            slug='changan-cs35-s1010140400',
            brand=self.changan,
            car_model=self.cs35,
            price=1760,
            stock_qty=10,
            compatibility='Changan CS35',
            oem_cross_references='S1010140400\nS101014-0400',
        )
        self.s101.selected_models.add(self.cs35)

        self.s111 = _product(
            article='S111F2801031700',
            title='Салонный фильтр Changan — S111F2801031700',
            slug='changan-s111f2801031700',
            brand=self.changan,
            price=2200,
            stock_qty=49,
            compatibility='Changan CS35 Plus, Eado Plus. CS35 без Plus и кроссы Ford/Volvo не входят.',
            oem_cross_references='S111F2801031700\nS111F280103-1700',
        )
        self.s111.selected_models.add(self.cs35_plus, self.eado_plus)

        self.xp24 = _product(
            article='8104400XP24BA',
            title='Салонный фильтр Great Wall Wingle 7 — 8104400XP24BA',
            slug=OLD_XP24_SLUG,
            brand=self.gwm,
            car_model=self.wingle7,
            price=1760,
            stock_qty=20,
            compatibility='Great Wall Wingle 7 (с 10.2018, 2.0), Wingle 6 (2014–2021, 2.4).',
            oem_cross_references='8104400XP24BA',
        )
        self.xp24.selected_models.add(self.wingle7, self.wingle6)
        ProductPriceTier.objects.create(product=self.xp24, min_qty=1, price=1760)

    def test_batch_06_has_no_research_notes(self):
        spec = load_batch('06')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_xp24_slug_redirect_is_registered(self):
        self.assertEqual(FITMENT_SLUG_REDIRECTS[OLD_XP24_SLUG], NEW_XP24_SLUG)

    def test_apply_batch_06_oem_engines_and_slug(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('06')), apply=True)

        self.x01.refresh_from_db()
        self.assertEqual(self.x01.price, 985)
        self.assertEqual(self.x01.stock_qty, 28)
        self.assertNotIn('X01-29150063', self.x01.oem_cross_references)
        self.assertNotIn('L2E15M', self.x01.engine_compatibility or '')
        self.assertEqual(
            set(self.x01.selected_models.values_list('name', flat=True)),
            {'L6', 'L7', 'L8', 'L9'},
        )
        x01_page = self.client.get('/li-auto-l7-x01-90000014-li-auto-l7/')
        self.assertEqual(x01_page.status_code, 200)
        self.assertContains(x01_page, 'X01-90000014')
        self.assertContains(x01_page, 'Не смешивать с X01-29150063')
        self.assertNotContains(x01_page, 'L2E15M')
        self.assertContains(x01_page, '<li>L6</li>')
        self.assertContains(x01_page, '<li>L9</li>')

        self.s101.refresh_from_db()
        self.assertEqual(self.s101.price, 1760)
        self.assertIn('JL478QEE', self.s101.engine_compatibility)
        self.assertIn('CS35 Plus', self.s101.compatibility)
        self.assertEqual(
            set(self.s101.selected_models.values_list('name', flat=True)),
            {'CS35'},
        )
        s101_page = self.client.get('/changan-cs35-s1010140400/')
        self.assertEqual(s101_page.status_code, 200)
        self.assertContains(s101_page, 'JL478QEP')
        self.assertContains(s101_page, 'CS35 Plus')
        self.assertNotContains(s101_page, '<li>CS35 Plus</li>')

        self.s111.refresh_from_db()
        self.assertEqual(self.s111.price, 2200)
        self.assertIn('JL473ZQ3', self.s111.engine_compatibility)
        self.assertIn('Ford/Volvo не входят', self.s111.compatibility)
        s111_page = self.client.get('/changan-s111f2801031700/')
        self.assertEqual(s111_page.status_code, 200)
        self.assertContains(s111_page, 'JL478QEP')
        self.assertContains(s111_page, 'Ford/Volvo не входят')
        self.assertNotContains(s111_page, 'Focus')

        self.xp24.refresh_from_db()
        self.assertEqual(self.xp24.slug, NEW_XP24_SLUG)
        self.assertEqual(self.xp24.price, 1760)
        self.assertEqual(self.xp24.stock_qty, 20)
        self.assertIn('GW4D20D', self.xp24.engine_compatibility)
        self.assertEqual(
            set(self.xp24.selected_models.values_list('name', flat=True)),
            {'Wingle 7'},
        )
        old = self.client.get(f'/{OLD_XP24_SLUG}/')
        self.assertEqual(old.status_code, 301)
        self.assertEqual(old['Location'], f'/{NEW_XP24_SLUG}/')
        new = self.client.get(f'/{NEW_XP24_SLUG}/')
        self.assertEqual(new.status_code, 200)
        self.assertContains(new, 'Wingle 6')
        self.assertContains(new, '4G69S4N')
        self.assertContains(new, f'https://zpt.kz/{NEW_XP24_SLUG}/')
        self.assertContains(new, '<li>Wingle 7</li>')
        self.assertNotContains(new, '<li>Wingle 6</li>')

        search = self.client.get('/', {'q': '8104400XP24BA'})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, f'/{NEW_XP24_SLUG}/')
        self.assertNotContains(search, f'/{OLD_XP24_SLUG}/')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '06', stdout=out)
        self.xp24.refresh_from_db()
        self.assertEqual(self.xp24.slug, OLD_XP24_SLUG)
        self.assertIn('dry-run', out.getvalue())
        self.x01.refresh_from_db()
        self.assertIn('X01-29150063', self.x01.oem_cross_references)
