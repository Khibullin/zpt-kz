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
        'article': 'FIT-B7',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch07Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B7')
        self.haval = Brand.objects.create(country=country, name='Haval')
        self.jac = Brand.objects.create(country=country, name='JAC')
        self.dargo = CarModel.objects.create(brand=self.haval, name='Dargo')
        self.f7 = CarModel.objects.create(brand=self.haval, name='F7')
        self.f7x = CarModel.objects.create(brand=self.haval, name='F7x')
        self.jolion = CarModel.objects.create(brand=self.haval, name='Jolion')
        self.s3 = CarModel.objects.create(brand=self.jac, name='S3')
        self.j7 = CarModel.objects.create(brand=self.jac, name='J7')
        self.js4 = CarModel.objects.create(brand=self.jac, name='JS4')
        self.t6 = CarModel.objects.create(brand=self.jac, name='T6')

        self.xky = _product(
            article='8104400XKY28B',
            title='Салонный фильтр Haval F7 — 8104400XKY28B',
            slug='haval-f7-8104400xky28b-haval-f7',
            brand=self.haval,
            car_model=self.f7,
            price=3410,
            stock_qty=24,
            compatibility='Haval F7, F7x, Dargo, Jolion.',
            oem_cross_references='8104400XKY28B',
        )
        self.xky.selected_models.add(self.dargo, self.f7, self.f7x, self.jolion)
        ProductPriceTier.objects.create(product=self.xky, min_qty=1, price=3410)

        self.p3010 = _product(
            article='8104102P3010',
            title='Салонный фильтр JAC — 8104102P3010',
            slug='jac-8104102p3010-jac',
            brand=self.jac,
            price=1800,
            stock_qty=26,
            compatibility='JAC T6 (Shuailing T6).',
            description='Основная применимость: JAC T6 (Shuailing T6).',
            oem_cross_references='8104102P3010',
        )
        self.p3010.selected_models.add(self.t6)

        self.u8520 = _product(
            article='8114010U8520',
            title='Салонный фильтр JAC S3 — 8114010U8520',
            slug='jac-s3-8114010u8520-jac-s3',
            brand=self.jac,
            car_model=self.s3,
            price=2189,
            stock_qty=20,
            compatibility='JAC S3, JS4, J7. Номера Hyundai/Kia 97133 не входят.',
            oem_cross_references='8114010U8520',
        )
        self.u8520.selected_models.add(self.s3, self.js4, self.j7)

    def test_batch_07_has_no_research_notes(self):
        spec = load_batch('07')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_07_engines_and_t8(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('07')), apply=True)

        self.xky.refresh_from_db()
        self.assertEqual(self.xky.price, 3410)
        self.assertEqual(self.xky.stock_qty, 24)
        self.assertIn('GW4B15A', self.xky.engine_compatibility)
        self.assertIn('XKY28A', self.xky.compatibility)
        self.assertEqual(
            set(self.xky.selected_models.values_list('name', flat=True)),
            {'Dargo', 'F7', 'F7x', 'Jolion'},
        )
        xky_page = self.client.get('/haval-f7-8104400xky28b-haval-f7/')
        self.assertEqual(xky_page.status_code, 200)
        self.assertContains(xky_page, 'GW4N20')
        self.assertContains(xky_page, 'VAG/Porsche не входят')
        self.assertNotContains(xky_page, 'Panamera')
        self.assertContains(xky_page, '<li>Dargo</li>')

        self.p3010.refresh_from_db()
        self.assertEqual(self.p3010.price, 1800)
        self.assertIn('HFC4DB21D1', self.p3010.engine_compatibility)
        self.assertIn('T8', self.p3010.compatibility)
        self.assertNotIn('T6', self.p3010.compatibility)
        self.assertNotIn('Shuailing', self.p3010.description)
        self.assertFalse(self.p3010.selected_models.exists())
        p3010_page = self.client.get('/jac-8104102p3010-jac/')
        self.assertEqual(p3010_page.status_code, 200)
        self.assertContains(p3010_page, 'T8')
        self.assertContains(p3010_page, 'HFC4DB21D1')
        self.assertNotContains(p3010_page, 'Shuailing')
        self.assertNotContains(p3010_page, 'Mazda 6')
        self.assertNotContains(p3010_page, '<li>T6</li>')

        self.u8520.refresh_from_db()
        self.assertEqual(self.u8520.price, 2189)
        self.assertIn('HFC4GB3-3D', self.u8520.engine_compatibility)
        self.assertIn('JS3', self.u8520.compatibility)
        self.assertEqual(
            set(self.u8520.selected_models.values_list('name', flat=True)),
            {'J7', 'JS4', 'S3'},
        )
        u8520_page = self.client.get('/jac-s3-8114010u8520-jac-s3/')
        self.assertEqual(u8520_page.status_code, 200)
        self.assertContains(u8520_page, 'HFC4GB24D')
        self.assertContains(u8520_page, '97133 не входят')
        self.assertNotContains(u8520_page, 'Accent')
        self.assertNotContains(u8520_page, '<li>JS3</li>')

        search = self.client.get('/', {'q': '8104102P3010'})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, '/jac-8104102p3010-jac/')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '07', stdout=out)
        self.p3010.refresh_from_db()
        self.assertIn('T6', self.p3010.compatibility)
        self.assertIn('dry-run', out.getvalue())
