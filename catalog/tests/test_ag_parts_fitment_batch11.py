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
        'article': 'FIT-B11',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch11Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B11')
        self.geely = Brand.objects.create(country=country, name='Geely')
        self.jac = Brand.objects.create(country=country, name='JAC')
        self.haval = Brand.objects.create(country=country, name='Haval')
        self.coolray = CarModel.objects.create(brand=self.geely, name='Coolray')
        self.js6 = CarModel.objects.create(brand=self.jac, name='JS6')
        self.h9 = CarModel.objects.create(brand=self.haval, name='H9')

        self.coolray_air = _product(
            article='6600131687',
            title='Воздушный фильтр Geely Coolray — 6600131687',
            slug='geely-coolray-6600131687-geely-coolray',
            brand=self.geely,
            car_model=self.coolray,
            price=2500,
            stock_qty=7,
            compatibility='Geely Coolray.',
        )
        self.coolray_air.selected_models.add(self.coolray)
        ProductPriceTier.objects.create(product=self.coolray_air, min_qty=1, price=2500)

        self.js6_air = _product(
            article='1109130U2400',
            title='Воздушный фильтр JAC JS6 — 1109130U2400',
            slug='jac-js6-1109130u2400-jac-js6',
            brand=self.jac,
            price=2200,
            stock_qty=4,
            compatibility='JAC JS6.',
        )
        ProductPriceTier.objects.create(product=self.js6_air, min_qty=1, price=2200)

        self.s2_air = _product(
            article='1109120U8710',
            title='Воздушный фильтр 1109120U8710',
            slug='1109120u8710',
            brand=self.jac,
            price=1800,
            stock_qty=5,
            compatibility='JAC S2 — 2015+, 1.5 л.',
        )

        self.h2_cabin = _product(
            article='8104400ASZ08A',
            title='Салонный фильтр Haval — 8104400ASZ08A',
            slug='haval-8104400asz08a-haval',
            brand=self.haval,
            price=2100,
            stock_qty=6,
            compatibility='Haval H2.',
        )

        self.h9_cabin = _product(
            article='8100103XKV08A',
            title='Салонный фильтр Haval H9 — 8100103XKV08A',
            slug='haval-h9-8100103xkv08a-haval-h9',
            brand=self.haval,
            car_model=self.h9,
            price=2300,
            stock_qty=8,
            compatibility='Haval H9.',
        )
        self.h9_cabin.selected_models.add(self.h9)

    def test_batch_11_has_no_research_notes(self):
        spec = load_batch('11')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_11_engines_and_models(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('11')), apply=True)

        self.coolray_air.refresh_from_db()
        self.assertEqual(self.coolray_air.price, 2500)
        self.assertIn('JLH-3G15TD', self.coolray_air.engine_compatibility)
        self.assertIn('2032040500', self.coolray_air.oem_cross_references)
        self.assertEqual(
            set(self.coolray_air.selected_models.values_list('name', flat=True)),
            {'Coolray'},
        )
        coolray_page = self.client.get('/geely-coolray-6600131687-geely-coolray/')
        self.assertEqual(coolray_page.status_code, 200)
        self.assertContains(coolray_page, 'BHE15-EFZ')
        self.assertContains(coolray_page, 'JLB-4G14TB')
        self.assertNotContains(coolray_page, 'салонный фильтр Geely Coolray', status_code=200)

        self.js6_air.refresh_from_db()
        self.assertIn('HFC4GC1.6E', self.js6_air.engine_compatibility)
        self.assertEqual(
            set(self.js6_air.selected_models.values_list('name', flat=True)),
            {'JS6'},
        )
        js6_page = self.client.get('/jac-js6-1109130u2400-jac-js6/')
        self.assertEqual(js6_page.status_code, 200)
        self.assertContains(js6_page, 'HFC4GC1.6E')
        self.assertNotContains(js6_page, 'HFC4GC1.6D')

        self.s2_air.refresh_from_db()
        self.assertEqual(self.s2_air.price, 1800)
        self.assertIn('HFC4GB2.3D', self.s2_air.engine_compatibility)
        self.assertFalse(self.s2_air.selected_models.exists())
        s2_page = self.client.get('/1109120u8710/')
        self.assertEqual(s2_page.status_code, 200)
        self.assertContains(s2_page, 'HFC4GB2.3D')

        self.h2_cabin.refresh_from_db()
        self.assertIn('GW4G15B', self.h2_cabin.engine_compatibility)
        self.assertFalse(self.h2_cabin.selected_models.exists())
        h2_page = self.client.get('/haval-8104400asz08a-haval/')
        self.assertEqual(h2_page.status_code, 200)
        self.assertContains(h2_page, 'H2')
        self.assertContains(h2_page, 'не подтверждаем')

        self.h9_cabin.refresh_from_db()
        self.assertIn('GW4C20B', self.h9_cabin.engine_compatibility)
        self.assertNotIn('8100103XKV08B', self.h9_cabin.oem_cross_references)
        self.assertEqual(
            set(self.h9_cabin.selected_models.values_list('name', flat=True)),
            {'H9'},
        )
        h9_page = self.client.get('/haval-h9-8100103xkv08a-haval-h9/')
        self.assertEqual(h9_page.status_code, 200)
        self.assertContains(h9_page, 'GW4D20T')

        search = self.client.get('/', {'q': '6600131687'})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, '/geely-coolray-6600131687-geely-coolray/')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '11', stdout=out)
        self.js6_air.refresh_from_db()
        self.assertFalse(self.js6_air.engine_compatibility)
        self.assertIn('dry-run', out.getvalue())
        self.assertIn('WOULD_CHANGE', out.getvalue())
