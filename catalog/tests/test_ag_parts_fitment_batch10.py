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


OLD_265_SLUG = 'chery-tiggo-8'
NEW_265_SLUG = 'salonnyi-filtr-301000265aa'


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'FIT-B10',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch10Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B10')
        self.chery = Brand.objects.create(country=country, name='Chery')
        self.geely = Brand.objects.create(country=country, name='Geely')
        self.byd = Brand.objects.create(country=country, name='BYD')
        self.exeed = Brand.objects.create(country=country, name='Exeed')
        self.tiggo2 = CarModel.objects.create(brand=self.chery, name='Tiggo 2')
        self.monjaro = CarModel.objects.create(brand=self.geely, name='Monjaro')
        self.tugella = CarModel.objects.create(brand=self.geely, name='Tugella')
        self.tang = CarModel.objects.create(brand=self.byd, name='Tang')
        self.txl = CarModel.objects.create(brand=self.exeed, name='TXL')
        self.vx = CarModel.objects.create(brand=self.exeed, name='VX')

        self.j69 = _product(
            article='J691109111',
            title='Воздушный фильтр J691109111',
            slug='j691109111',
            brand=self.chery,
            price=1980,
            stock_qty=26,
            compatibility='Chery Tiggo 2 — 2016–2021',
            oem_cross_references='ADBP220237\nELP9720\nSXJ691109111',
        )
        ProductPriceTier.objects.create(product=self.j69, min_qty=1, price=1980)

        self.geely_air = _product(
            article='2032047000',
            title='Воздушный фильтр 2032047000',
            slug='2032047000',
            brand=self.geely,
            price=4190,
            stock_qty=10,
            compatibility=(
                'Geely Monjaro — 2022–2024, 2.0T; Geely Tugella — 2.0T; '
                'Geely Xingyue L — с 2021 года, 2.0T; Geely Preface — с 2020 года, 2.0T; '
                'с 2023 года, 1.5T; Lynk & Co 05 — с 2020 года, 2.0T.'
            ),
        )
        ProductPriceTier.objects.create(product=self.geely_air, min_qty=1, price=4190)

        self.tang_cabin = _product(
            article='13033898-00',
            title='Салонный фильтр BYD Tang — 13033898-00',
            slug='byd-tang-13033898-00',
            brand=self.byd,
            car_model=self.tang,
            price=2250,
            stock_qty=15,
            compatibility='BYD Tang (включая EV и гибридные версии).',
            oem_cross_references='13033898-00\n1303389800',
            description='Han / Yuan Pro / Song у витрин не подтверждены.',
        )
        self.tang_cabin.selected_models.add(self.tang)
        ProductPriceTier.objects.create(product=self.tang_cabin, min_qty=1, price=2250)

        self.cabin265 = _product(
            article='301000265AA',
            title='Салонный фильтр Exeed TXL — 301000265AA',
            slug=OLD_265_SLUG,
            brand=self.exeed,
            car_model=self.txl,
            price=1650,
            stock_qty=55,
            compatibility='Exeed TXL, Exeed VX.',
            description='Пылевой. Антибактериальный 301001199AA — другой артикул.',
            oem_cross_references='301000265AA',
        )
        self.cabin265.selected_models.add(self.txl, self.vx)
        ProductPriceTier.objects.create(product=self.cabin265, min_qty=1, price=1650)

    def test_265_slug_redirect_is_registered(self):
        self.assertEqual(FITMENT_SLUG_REDIRECTS[OLD_265_SLUG], NEW_265_SLUG)
        self.assertEqual(
            FITMENT_SLUG_REDIRECTS['chery-tiggo-8-2'],
            'salonnyi-filtr-301001199aa',
        )

    def test_batch_10_has_no_research_notes(self):
        spec = load_batch('10')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_10_engines_oem_and_slug(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('10')), apply=True)

        self.j69.refresh_from_db()
        self.assertEqual(self.j69.price, 1980)
        self.assertEqual(self.j69.stock_qty, 26)
        self.assertEqual(self.j69.slug, 'j691109111')
        self.assertIn('SQRD4G15B', self.j69.engine_compatibility)
        self.assertIn('J69-1109111', self.j69.oem_cross_references)
        self.assertNotIn('ADBP220237', self.j69.oem_cross_references)
        self.assertEqual(
            set(self.j69.selected_models.values_list('name', flat=True)),
            {'Tiggo 2'},
        )
        j69_page = self.client.get('/j691109111/')
        self.assertEqual(j69_page.status_code, 200)
        self.assertContains(j69_page, 'SQRD4G15B')
        self.assertContains(j69_page, 'Tiggo 2')
        self.assertNotContains(j69_page, 'ADBP220237')
        self.assertNotContains(j69_page, '2016–2021')

        self.geely_air.refresh_from_db()
        self.assertEqual(self.geely_air.price, 4190)
        self.assertEqual(self.geely_air.stock_qty, 10)
        self.assertEqual(self.geely_air.slug, '2032047000')
        self.assertIn('JLH-3G15TD', self.geely_air.engine_compatibility)
        self.assertIn('BHE15-EFZ', self.geely_air.engine_compatibility)
        self.assertIn('8888475602', self.geely_air.oem_cross_references)
        self.assertEqual(
            set(self.geely_air.selected_models.values_list('name', flat=True)),
            {'Monjaro', 'Tugella'},
        )
        geely_page = self.client.get('/2032047000/')
        self.assertEqual(geely_page.status_code, 200)
        self.assertContains(geely_page, 'JLH-4G20TDJ')
        self.assertContains(geely_page, 'Preface')
        self.assertContains(geely_page, 'Atlas L')
        self.assertContains(geely_page, 'не подтверждаем')
        self.assertNotContains(geely_page, 'с 2020 года')
        self.assertNotContains(geely_page, '2032061800</li>')

        self.tang_cabin.refresh_from_db()
        self.assertEqual(self.tang_cabin.price, 2250)
        self.assertIn('BYD487ZQA', self.tang_cabin.engine_compatibility)
        self.assertIn('ST-8121211E-E1', self.tang_cabin.oem_cross_references)
        self.assertNotIn('EM2E-8121211E', self.tang_cabin.oem_cross_references)
        self.assertEqual(
            set(self.tang_cabin.selected_models.values_list('name', flat=True)),
            {'Tang'},
        )
        tang_page = self.client.get('/%s/' % 'byd-tang-13033898-00')
        self.assertEqual(tang_page.status_code, 200)
        self.assertContains(tang_page, 'BYD476ZQC')
        self.assertContains(tang_page, 'Tang EV')
        self.assertContains(tang_page, 'Не смешивать с EM2E-8121211E')

        self.cabin265.refresh_from_db()
        self.assertEqual(self.cabin265.slug, NEW_265_SLUG)
        self.assertEqual(self.cabin265.price, 1650)
        self.assertEqual(self.cabin265.stock_qty, 55)
        self.assertEqual(self.cabin265.title, 'Салонный фильтр Exeed TXL — 301000265AA')
        self.assertIn('301001199AA', self.cabin265.description)
        self.assertEqual(
            set(self.cabin265.selected_models.values_list('name', flat=True)),
            {'TXL', 'VX'},
        )

        old = self.client.get(f'/{OLD_265_SLUG}/', follow=False)
        self.assertEqual(old.status_code, 301)
        self.assertEqual(old['Location'], f'/{NEW_265_SLUG}/')
        new = self.client.get(f'/{NEW_265_SLUG}/')
        self.assertEqual(new.status_code, 200)
        self.assertContains(new, '301000265AA')
        self.assertContains(new, 'TXL')
        self.assertContains(new, 'VX')
        self.assertNotContains(new, '<li>Tiggo 8</li>')

        search = self.client.get('/', {'q': 'J691109111'})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, '/j691109111/')

        search_265 = self.client.get('/', {'q': '301000265AA'})
        self.assertEqual(search_265.status_code, 200)
        self.assertContains(search_265, f'/{NEW_265_SLUG}/')
        self.assertNotContains(search_265, f'href="/{OLD_265_SLUG}/"')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '10', stdout=out)
        self.j69.refresh_from_db()
        self.cabin265.refresh_from_db()
        self.assertEqual(self.j69.oem_cross_references, 'ADBP220237\nELP9720\nSXJ691109111')
        self.assertEqual(self.cabin265.slug, OLD_265_SLUG)
        self.assertIn('dry-run', out.getvalue())
        self.assertIn('WOULD_CHANGE', out.getvalue())
