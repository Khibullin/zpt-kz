from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from io import StringIO

from catalog.ag_parts_fitment_audit import (
    apply_fitment_plans,
    load_batch,
    plan_fitment_batch,
)
from catalog.fitment_slug_redirects import FITMENT_SLUG_REDIRECTS
from catalog.models import Brand, CarModel, Country, Product, ProductPriceTier
from catalog.product_quality import detect_internal_research_text


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'FIT-B3',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch03Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B3')
        self.chery = Brand.objects.create(country=country, name='Chery')
        self.exeed = Brand.objects.create(country=country, name='Exeed')
        self.omoda = Brand.objects.create(country=country, name='Omoda')
        self.haval = Brand.objects.create(country=country, name='Haval')
        self.changan = Brand.objects.create(country=country, name='Changan')
        self.tiggo4 = CarModel.objects.create(brand=self.chery, name='Tiggo 4')
        self.tiggo4_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 4 Pro')
        self.tiggo7 = CarModel.objects.create(brand=self.chery, name='Tiggo 7')
        self.tiggo7_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro')
        self.tiggo8 = CarModel.objects.create(brand=self.chery, name='Tiggo 8')
        self.lx = CarModel.objects.create(brand=self.exeed, name='LX')
        self.c5 = CarModel.objects.create(brand=self.omoda, name='C5')
        self.dargo = CarModel.objects.create(brand=self.haval, name='Dargo')
        self.f7 = CarModel.objects.create(brand=self.haval, name='F7')
        self.f7x = CarModel.objects.create(brand=self.haval, name='F7x')
        self.h6 = CarModel.objects.create(brand=self.haval, name='H6')
        self.h9 = CarModel.objects.create(brand=self.haval, name='H9')
        self.jolion = CarModel.objects.create(brand=self.haval, name='Jolion')
        self.univ = CarModel.objects.create(brand=self.changan, name='UNI-V')
        self.unik = CarModel.objects.create(brand=self.changan, name='UNI-K')
        self.unit = CarModel.objects.create(brand=self.changan, name='UNI-T')
        self.cs55 = CarModel.objects.create(brand=self.changan, name='CS55')

        self.t151 = _product(
            article='T151109111',
            title='Воздушный фильтр Chery Tiggo 7 — T151109111',
            slug='chery-tiggo-7-t151109111',
            brand=self.chery,
            car_model=self.tiggo7,
            price=1700,
            stock_qty=14,
            compatibility='Chery Tiggo 4, Tiggo 7, Tiggo 7 Pro, Tiggo 8; Exeed LX; Omoda C5',
            description='Основная применимость: Chery Tiggo 4, Tiggo 7, Tiggo 7 Pro, Tiggo 8.',
        )
        self.t151.selected_models.add(
            self.tiggo4, self.tiggo7, self.tiggo7_pro, self.tiggo8, self.lx, self.c5,
        )
        ProductPriceTier.objects.create(product=self.t151, min_qty=1, price=1700)

        self.dargo_air = _product(
            article='1109101XGW01A',
            title='Воздушный фильтр Haval Dargo — 1109101XGW01A',
            slug='haval-dargo-1109101xgw01a-haval-dargo',
            brand=self.haval,
            car_model=self.dargo,
            price=1150,
            stock_qty=10,
            compatibility='Haval Dargo, F7, F7x',
            description='Основная применимость: Haval Dargo, F7, F7x.',
        )
        self.dargo_air.selected_models.add(self.dargo, self.f7, self.f7x)

        self.s301 = _product(
            article='S3010140903',
            title='Воздушный фильтр Changan UNI-V — S3010140903',
            slug='changan-uni-v-s3010140903-changan-uni-v',
            brand=self.changan,
            car_model=self.univ,
            price=890,
            stock_qty=8,
            compatibility='Changan UNI-V',
        )
        self.s301.selected_models.add(self.univ)

        self.cr01 = _product(
            article='1109190CR01',
            title='Воздушный фильтр Changan UNI-K — 1109190CR01',
            slug='changan-uni-k-1109190cr01',
            brand=self.changan,
            car_model=self.unik,
            price=2750,
            stock_qty=12,
            compatibility='Changan UNI-K',
        )
        self.cr01.selected_models.add(self.unik)

        self.h9_air = _product(
            article='1109110XKV08A',
            title='Воздушный фильтр 1109110XKV08A Haval H6',
            slug=(
                '1109110xkv08a-1109110xkv08a-1109110xkv08a-'
                '1109110xkv08a-1109110xkv08a-1109110xkv08a-1109-haval-h6'
            ),
            brand=self.haval,
            car_model=self.h6,
            price=2310,
            stock_qty=6,
            compatibility='Haval H6',
            description='Подходит для всех годов выпуска Haval H9 и Haval H6.',
        )
        self.h9_air.selected_models.add(self.h6)

        self.jolion_air = _product(
            article='1109104XGW02A',
            title='Воздушный фильтр Haval Jolion — 1109104XGW02A',
            slug='haval-jolion-1109104xgw02a-haval-jolion',
            brand=self.haval,
            car_model=self.jolion,
            price=1400,
            stock_qty=11,
            compatibility='Haval Jolion (с 2021).',
        )
        self.jolion_air.selected_models.add(self.jolion)

    def test_batch_03_has_no_research_notes(self):
        spec = load_batch('03')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_03_engines_and_h9_slug(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('03')), apply=True)

        self.t151.refresh_from_db()
        self.assertIn('SQRE4G15B', self.t151.engine_compatibility)
        self.assertIn('SQRE4T15C', self.t151.compatibility)
        self.assertNotIn('Tiggo 4 Pro', self.t151.compatibility)
        names = set(self.t151.selected_models.values_list('name', flat=True))
        self.assertIn('Tiggo 4', names)
        self.assertNotIn('Tiggo 4 Pro', names)
        self.assertEqual(self.t151.price, 1700)
        self.assertEqual(self.t151.stock_qty, 14)
        self.assertEqual(self.t151.price_tiers.get().price, 1700)
        self.assertEqual(self.t151.slug, 'chery-tiggo-7-t151109111')

        self.dargo_air.refresh_from_db()
        self.assertIn('F7 II', self.dargo_air.title)
        self.assertIn('GW4B15L', self.dargo_air.engine_compatibility)
        self.assertIn('не подтверждаем', self.dargo_air.compatibility)
        self.assertNotIn('H6', self.dargo_air.compatibility)
        self.assertEqual(
            set(self.dargo_air.selected_models.values_list('name', flat=True)),
            {'Dargo', 'F7', 'F7x'},
        )
        self.assertEqual(self.dargo_air.price, 1150)

        self.s301.refresh_from_db()
        self.assertIn('JL473ZQ7', self.s301.engine_compatibility)
        self.assertNotIn('CS55', self.s301.compatibility)
        self.assertEqual(
            list(self.s301.selected_models.values_list('name', flat=True)),
            ['UNI-V'],
        )

        self.cr01.refresh_from_db()
        self.assertIn('JL486ZQ5', self.cr01.engine_compatibility)
        self.assertIn('UNI-K iDD', self.cr01.compatibility)
        self.assertNotIn('UNI-V 1.5', self.cr01.compatibility)
        self.assertNotIn(self.unit, list(self.cr01.selected_models.all()))
        self.assertNotIn(self.cs55, list(self.cr01.selected_models.all()))

        self.h9_air.refresh_from_db()
        self.assertEqual(self.h9_air.car_model, self.h9)
        self.assertEqual(
            list(self.h9_air.selected_models.values_list('name', flat=True)),
            ['H9'],
        )
        self.assertNotIn('H6', self.h9_air.title)
        self.assertIn('не подтверждаем', self.h9_air.description)
        self.assertEqual(self.h9_air.slug, 'vozdushnyi-filtr-1109110xkv08a')
        self.assertEqual(self.h9_air.price, 2310)

        self.jolion_air.refresh_from_db()
        self.assertIn('GW4G15K', self.jolion_air.engine_compatibility)
        self.assertEqual(self.jolion_air.price, 1400)

        old_h9 = self.client.get(
            '/1109110xkv08a-1109110xkv08a-1109110xkv08a-'
            '1109110xkv08a-1109110xkv08a-1109110xkv08a-1109-haval-h6/'
        )
        self.assertEqual(old_h9.status_code, 301)
        self.assertEqual(old_h9['Location'], '/vozdushnyi-filtr-1109110xkv08a/')
        new_h9 = self.client.get('/vozdushnyi-filtr-1109110xkv08a/')
        self.assertEqual(new_h9.status_code, 200)
        self.assertContains(new_h9, 'Haval H9')
        self.assertNotContains(new_h9, '<li>H6</li>')
        self.assertContains(new_h9, 'https://zpt.kz/vozdushnyi-filtr-1109110xkv08a/')

        t151_page = self.client.get(reverse('product_detail', kwargs={'slug': self.t151.slug}))
        self.assertContains(t151_page, 'SQRE4G15B')
        self.assertContains(t151_page, '<li>Tiggo 4</li>')
        self.assertNotContains(t151_page, '<li>Tiggo 4 Pro</li>')

        dargo_page = self.client.get(
            reverse('product_detail', kwargs={'slug': self.dargo_air.slug})
        )
        self.assertContains(dargo_page, 'F7 II')
        self.assertContains(dargo_page, 'GW4N20')

    def test_h9_slug_redirect_is_registered(self):
        self.assertEqual(
            FITMENT_SLUG_REDIRECTS[
                '1109110xkv08a-1109110xkv08a-1109110xkv08a-'
                '1109110xkv08a-1109110xkv08a-1109110xkv08a-1109-haval-h6'
            ],
            'vozdushnyi-filtr-1109110xkv08a',
        )
        self.assertEqual(
            self.h9_air.get_absolute_url(),
            '/vozdushnyi-filtr-1109110xkv08a/',
        )

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '03', stdout=out)
        self.h9_air.refresh_from_db()
        self.assertIn('haval-h6', self.h9_air.slug)
        self.assertIn('dry-run', out.getvalue())
