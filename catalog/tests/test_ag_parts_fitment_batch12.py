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


OLD_025_SLUG = 'chery-tiggo-7-2'
NEW_025_SLUG = 'vozdushnyi-filtr-151000025aa'


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'FIT-B12',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch12Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B12')
        self.exeed = Brand.objects.create(country=country, name='Exeed')
        self.gwm = Brand.objects.create(country=country, name='Great Wall')
        self.tank = Brand.objects.create(country=country, name='Tank')
        self.haval = Brand.objects.create(country=country, name='Haval')
        self.txl = CarModel.objects.create(brand=self.exeed, name='TXL')
        self.vx = CarModel.objects.create(brand=self.exeed, name='VX')
        self.poer = CarModel.objects.create(brand=self.gwm, name='Poer')
        self.tank300 = CarModel.objects.create(brand=self.tank, name='300')
        self.h5 = CarModel.objects.create(brand=self.haval, name='H5')
        self.wingle7 = CarModel.objects.create(brand=self.gwm, name='Wingle 7')

        self.air025 = _product(
            article='151000025AA',
            title='Воздушный фильтр Exeed TXL — 151000025AA',
            slug=OLD_025_SLUG,
            brand=self.exeed,
            car_model=self.txl,
            price=2450,
            stock_qty=12,
            compatibility='Exeed TXL only.',
        )
        self.air025.selected_models.add(self.txl)
        ProductPriceTier.objects.create(product=self.air025, min_qty=1, price=2450)

        self.air187 = _product(
            article='151000187AA',
            title='Воздушный фильтр Exeed TXL — 151000187AA',
            slug='exeed-txl-151000187aa-exeed-txl',
            brand=self.exeed,
            price=2680,
            stock_qty=8,
            compatibility='Exeed TXL.',
        )
        ProductPriceTier.objects.create(product=self.air187, min_qty=1, price=2680)

        self.cabin = _product(
            article='8100422XNZ01A',
            title='Салонный фильтр Great Wall Poer — 8100422XNZ01A',
            slug='great-wall-poer',
            brand=self.gwm,
            car_model=self.poer,
            price=3740,
            stock_qty=6,
            compatibility='Great Wall Poer; Tank 300.',
        )
        self.cabin.selected_models.add(self.poer)
        ProductPriceTier.objects.create(product=self.cabin, min_qty=1, price=3740)

        self.oil = _product(
            article='1017110XED95',
            title='Масляный фильтр Great Wall Poer — 1017110XED95',
            slug='great-wall-poer-1017110xed95',
            brand=self.gwm,
            car_model=self.poer,
            price=1890,
            stock_qty=15,
            compatibility='Great Wall Poer, Wingle 7 — дизель 2.0.',
        )
        self.oil.selected_models.add(self.poer, self.wingle7)
        ProductPriceTier.objects.create(product=self.oil, min_qty=1, price=1890)

    def test_batch_12_slug_map_and_no_research_notes(self):
        self.assertEqual(FITMENT_SLUG_REDIRECTS[OLD_025_SLUG], NEW_025_SLUG)
        spec = load_batch('12')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_12_engines_slug_and_models(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('12')), apply=True)

        self.air025.refresh_from_db()
        self.assertEqual(self.air025.price, 2450)
        self.assertEqual(self.air025.slug, NEW_025_SLUG)
        self.assertIn('SQRF4J16A', self.air025.engine_compatibility)
        self.assertIn('SQRF4J16D', self.air025.engine_compatibility)
        self.assertNotIn('100001222', self.air025.oem_cross_references)
        self.assertEqual(
            set(self.air025.selected_models.values_list('name', flat=True)),
            {'TXL'},
        )
        old = self.client.get(f'/{OLD_025_SLUG}/', follow=False)
        self.assertEqual(old.status_code, 301)
        self.assertEqual(old['Location'], f'/{NEW_025_SLUG}/')
        new = self.client.get(f'/{NEW_025_SLUG}/')
        self.assertEqual(new.status_code, 200)
        self.assertContains(new, 'SQRF4J16A')
        self.assertNotContains(new, 'Qoros')
        self.assertNotContains(new, 'Tiggo 8 Pro')

        self.air187.refresh_from_db()
        self.assertEqual(self.air187.price, 2680)
        self.assertIn('SQRF4J20C', self.air187.engine_compatibility)
        self.assertEqual(
            set(self.air187.selected_models.values_list('name', flat=True)),
            {'TXL', 'VX'},
        )
        page187 = self.client.get('/exeed-txl-151000187aa-exeed-txl/')
        self.assertEqual(page187.status_code, 200)
        self.assertContains(page187, 'M36T')
        self.assertContains(page187, '151000025AA')

        self.cabin.refresh_from_db()
        self.assertEqual(self.cabin.price, 3740)
        self.assertEqual(self.cabin.slug, 'great-wall-poer')
        self.assertIn('GW4C20B', self.cabin.engine_compatibility)
        self.assertIn('E20CB', self.cabin.engine_compatibility)
        self.assertNotIn('EMS1-19G245-AA', self.cabin.oem_cross_references)
        self.assertEqual(
            set(self.cabin.selected_models.values_list('name', flat=True)),
            {'Poer', '300'},
        )
        cabin_page = self.client.get('/great-wall-poer/')
        self.assertEqual(cabin_page.status_code, 200)
        self.assertContains(cabin_page, 'GW4D24')
        self.assertContains(cabin_page, 'E24D')

        self.oil.refresh_from_db()
        self.assertEqual(self.oil.price, 1890)
        self.assertIn('GW4D20M', self.oil.engine_compatibility)
        self.assertNotIn('1017110XEN01', self.oil.oem_cross_references)
        self.assertEqual(
            set(self.oil.selected_models.values_list('name', flat=True)),
            {'Poer', 'H5'},
        )
        oil_page = self.client.get('/great-wall-poer-1017110xed95/')
        self.assertEqual(oil_page.status_code, 200)
        self.assertContains(oil_page, 'H5')
        self.assertContains(oil_page, 'не подтверждаем')
        self.assertNotContains(oil_page, 'Wingle 7 — дизель')

        search = self.client.get('/', {'q': '151000025AA'})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, f'/{NEW_025_SLUG}/')
        self.assertNotContains(search, f'href="/{OLD_025_SLUG}/"')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '12', stdout=out)
        self.air025.refresh_from_db()
        self.oil.refresh_from_db()
        self.assertEqual(self.air025.slug, OLD_025_SLUG)
        self.assertIn('Wingle 7', self.oil.compatibility)
        self.assertIn('dry-run', out.getvalue())
        self.assertIn('WOULD_CHANGE', out.getvalue())
