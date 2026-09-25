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
        'article': 'FIT-B8',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch08Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B8')
        self.nissan = Brand.objects.create(country=country, name='Nissan')
        self.peugeot = Brand.objects.create(country=country, name='Peugeot')
        self.jetour = Brand.objects.create(country=country, name='Jetour')
        self.peugeot_308 = CarModel.objects.create(brand=self.peugeot, name='308')
        self.dashing = CarModel.objects.create(brand=self.jetour, name='Dashing')

        self.nissan_cabin = _product(
            article='272774M400',
            title='Салонный фильтр Nissan — 272774M400',
            slug='nissan-272774m400-nissan',
            brand=self.nissan,
            price=2250,
            stock_qty=12,
            compatibility='Nissan Altima 2002–2006, Nissan Maxima 2004–2008.',
            oem_cross_references='272774M400\n27277-4M400\n27277-VP01A',
            description='OEM: 272774M400; 27277-VP01A. Nissan Altima 2002–2006.',
        )
        self.nissan_cabin.selected_models.add(self.dashing)
        ProductPriceTier.objects.create(product=self.nissan_cabin, min_qty=1, price=2250)

        self.mann = _product(
            article='HU71151X',
            title='Масляный фильтр Peugeot 308 — HU71151X',
            slug='peugeot-308-hu71151x',
            brand=self.peugeot,
            car_model=self.peugeot_308,
            price=2200,
            stock_qty=8,
            compatibility='Peugeot 308; Citroen C4 Picasso / C5 II / DS4; Ford Transit 2007; MINI Cooper II — по официальному каталогу Mann.',
            oem_cross_references='HU71151X\nHU\n711/51\n1109AH\n1109AJ\n1109CK',
        )
        self.mann.selected_models.add(self.peugeot_308)

    def test_batch_08_has_no_research_notes(self):
        spec = load_batch('08')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_08_nissan_engines_and_mann_oem(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('08')), apply=True)

        self.nissan_cabin.refresh_from_db()
        self.assertEqual(self.nissan_cabin.price, 2250)
        self.assertEqual(self.nissan_cabin.stock_qty, 12)
        self.assertIn('QR25DE', self.nissan_cabin.engine_compatibility)
        self.assertNotIn('VP01A', self.nissan_cabin.oem_cross_references)
        self.assertFalse(self.nissan_cabin.selected_models.exists())
        nissan_page = self.client.get('/nissan-272774m400-nissan/')
        self.assertEqual(nissan_page.status_code, 200)
        self.assertContains(nissan_page, 'QR25DE')
        self.assertContains(nissan_page, 'VQ35DE')
        self.assertContains(nissan_page, 'Ford/Geely/Jetour не входят')
        self.assertNotContains(nissan_page, 'Dashing')
        self.assertNotContains(nissan_page, 'SQRF4J16A')

        self.mann.refresh_from_db()
        self.assertEqual(self.mann.price, 2200)
        self.assertIn('EP6', self.mann.engine_compatibility)
        self.assertNotIn('\nHU\n', '\n' + self.mann.oem_cross_references + '\n')
        self.assertNotIn('711/51\n', self.mann.oem_cross_references.replace('HU 711/51 X', ''))
        self.assertEqual(
            set(self.mann.selected_models.values_list('name', flat=True)),
            {'308'},
        )
        mann_page = self.client.get('/peugeot-308-hu71151x/')
        self.assertEqual(mann_page.status_code, 200)
        self.assertContains(mann_page, 'EP6CDT')
        self.assertContains(mann_page, 'HU 711/51 X')
        self.assertContains(mann_page, 'C4 Picasso')
        self.assertContains(mann_page, 'MINI Cooper II')
        self.assertNotContains(mann_page, '<li>HU</li>')
        self.assertNotContains(mann_page, '<li>711/51</li>')
        self.assertNotContains(mann_page, 'Ford Transit')
        self.assertNotContains(mann_page, 'DS4')
        self.assertNotContains(mann_page, 'Chery Tiggo')

        search = self.client.get('/', {'q': '272774M400'})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, '/nissan-272774m400-nissan/')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '08', stdout=out)
        self.nissan_cabin.refresh_from_db()
        self.assertIn('VP01A', self.nissan_cabin.oem_cross_references)
        self.assertIn('dry-run', out.getvalue())
