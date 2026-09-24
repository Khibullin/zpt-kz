from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from io import StringIO

from catalog.ag_parts_fitment_audit import (
    STATUS_CHANGED,
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
        'article': 'FIT-B2',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class FitmentSlugRedirectTests(TestCase):
    def test_old_urls_redirect_permanently(self):
        self.assertGreaterEqual(len(FITMENT_SLUG_REDIRECTS), 4)
        self.assertEqual(
            FITMENT_SLUG_REDIRECTS['chery-tiggo-7-pro-151000079aa-chery-tiggo-7-pro'],
            'vozdushnyi-filtr-151000079aa',
        )
        self.assertEqual(
            FITMENT_SLUG_REDIRECTS['chery-tiggo-7-f4j163707010-chery-tiggo-7'],
            'svecha-f4j163707010',
        )
        self.assertEqual(
            FITMENT_SLUG_REDIRECTS['changan-cs55'],
            'masljanyi-filtr-4801012010',
        )
        self.assertEqual(
            FITMENT_SLUG_REDIRECTS['chery-tiggo-8-2'],
            'salonnyi-filtr-301001199aa',
        )
        for old_slug, new_slug in FITMENT_SLUG_REDIRECTS.items():
            response = self.client.get(f'/{old_slug}/')
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response['Location'], f'/{new_slug}/')


class AgPartsFitmentBatch02Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B2')
        self.chery = Brand.objects.create(country=country, name='Chery')
        self.exeed = Brand.objects.create(country=country, name='Exeed')
        self.changan = Brand.objects.create(country=country, name='Changan')
        self.omoda = Brand.objects.create(country=country, name='Omoda')
        self.jaecoo = Brand.objects.create(country=country, name='Jaecoo')
        self.tiggo4 = CarModel.objects.create(brand=self.chery, name='Tiggo 4')
        self.tiggo7 = CarModel.objects.create(brand=self.chery, name='Tiggo 7')
        self.tiggo7_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro')
        self.tiggo7_pro_max = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro Max')
        self.tiggo8 = CarModel.objects.create(brand=self.chery, name='Tiggo 8')
        self.tiggo8_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 8 Pro')
        self.arrizo8 = CarModel.objects.create(brand=self.chery, name='Arrizo 8')
        self.txl = CarModel.objects.create(brand=self.exeed, name='TXL')
        self.vx = CarModel.objects.create(brand=self.exeed, name='VX')
        self.lx = CarModel.objects.create(brand=self.exeed, name='LX')
        self.j7 = CarModel.objects.create(brand=self.jaecoo, name='J7')
        self.c5 = CarModel.objects.create(brand=self.omoda, name='C5')
        self.univ = CarModel.objects.create(brand=self.changan, name='UNI-V')
        self.unit = CarModel.objects.create(brand=self.changan, name='UNI-T')

        self.t218 = _product(
            article='T218107011',
            title='Салонный фильтр Chery Tiggo 7 — T218107011',
            slug='chery-tiggo-7-t218107011-chery-tiggo-7',
            brand=self.chery,
            car_model=self.tiggo7,
            price=1650,
            stock_qty=11,
            description='Tiggo 8 Pro использует 301001199AA.',
        )
        self.t218.selected_models.add(
            self.arrizo8, self.tiggo4, self.tiggo7, self.tiggo7_pro, self.tiggo8,
        )
        ProductPriceTier.objects.create(product=self.t218, min_qty=1, price=1650)

        self.cabin199 = _product(
            article='301001199AA',
            title='Салонный фильтр Exeed TXL — 301001199AA',
            slug='chery-tiggo-8-2',
            brand=self.exeed,
            car_model=self.txl,
            price=1532,
            stock_qty=7,
            description='Exeed TXL, Exeed VX; Chery Tiggo 8 Pro.',
        )
        self.cabin199.selected_models.add(self.tiggo8_pro, self.txl, self.vx)

        self.oil480 = _product(
            article='4801012010',
            title='Масляный фильтр Chery Tiggo 7 — 4801012010',
            slug='changan-cs55',
            brand=self.chery,
            car_model=self.tiggo7,
            price=1309,
            stock_qty=15,
            compatibility='Chery Tiggo 4, Tiggo 7, Tiggo 7 Pro, Tiggo 8',
        )
        self.oil480.selected_models.add(
            self.tiggo4, self.tiggo7, self.tiggo7_pro, self.tiggo8,
        )

        self.c281 = _product(
            article='C281F2801032601',
            title='Салонный фильтр Changan UNI-V — C281F2801032601',
            slug='changan-uni-v-c281f2801032601',
            brand=self.changan,
            car_model=self.univ,
            price=1532,
            stock_qty=6,
            compatibility='Changan UNI-V, UNI-T',
        )
        self.c281.selected_models.add(self.univ, self.unit)

        self.air079 = _product(
            article='151000079AA',
            title='Воздушный фильтр Chery Tiggo 8 Pro 1.6 / Arrizo 8 — 151000079AA',
            slug='chery-tiggo-7-pro-151000079aa-chery-tiggo-7-pro',
            brand=self.chery,
            car_model=self.tiggo8_pro,
            price=1000,
            stock_qty=8,
        )
        self.air079.selected_models.add(
            self.tiggo8_pro, self.tiggo7_pro_max, self.arrizo8, self.lx, self.j7, self.c5,
        )

        self.spark = _product(
            article='F4J163707010',
            title='Свеча зажигания Chery Tiggo 8 1.5T SQRE4T15B / EXEED TXL — F4J163707010',
            slug='chery-tiggo-7-f4j163707010-chery-tiggo-7',
            brand=self.chery,
            car_model=self.tiggo8,
            price=2450,
            stock_qty=20,
        )
        self.spark.selected_models.add(self.tiggo8, self.txl, self.lx, self.c5)

    def test_batch_02_has_no_research_notes(self):
        spec = load_batch('02')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_02_and_slug_redirects(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('02')), apply=True)

        self.t218.refresh_from_db()
        self.assertIn('Tiggo 8 Pro', self.t218.title)
        self.assertNotIn('использует 301001199AA', self.t218.description)
        self.assertIn(self.tiggo8_pro, list(self.t218.selected_models.all()))
        self.assertEqual(self.t218.price, 1650)
        self.assertEqual(self.t218.stock_qty, 11)
        self.assertEqual(self.t218.price_tiers.get().price, 1650)

        self.cabin199.refresh_from_db()
        names = set(self.cabin199.selected_models.values_list('name', flat=True))
        self.assertNotIn('Tiggo 8 Pro', names)
        self.assertIn('TXL', names)
        self.assertEqual(self.cabin199.slug, 'salonnyi-filtr-301001199aa')
        self.assertEqual(self.cabin199.price, 1532)

        self.oil480.refresh_from_db()
        self.assertIn('Arrizo 8', set(self.oil480.selected_models.values_list('name', flat=True)))
        self.assertNotIn('Tiggo 8 Pro', set(self.oil480.selected_models.values_list('name', flat=True)))
        self.assertIn('F4J16-1012030', self.oil480.description)
        self.assertEqual(self.oil480.slug, 'masljanyi-filtr-4801012010')
        self.assertEqual(self.oil480.price, 1309)

        self.c281.refresh_from_db()
        self.assertIn('UNI-T', self.c281.title)
        self.assertIn('JL473ZQ5', self.c281.engine_compatibility)
        self.assertEqual(self.c281.price, 1532)

        self.air079.refresh_from_db()
        self.assertEqual(self.air079.slug, 'vozdushnyi-filtr-151000079aa')
        self.assertEqual(self.air079.price, 1000)
        self.spark.refresh_from_db()
        self.assertEqual(self.spark.slug, 'svecha-f4j163707010')
        self.assertEqual(self.spark.price, 2450)

        old_air = self.client.get('/chery-tiggo-7-pro-151000079aa-chery-tiggo-7-pro/')
        self.assertEqual(old_air.status_code, 301)
        self.assertEqual(old_air['Location'], '/vozdushnyi-filtr-151000079aa/')
        new_air = self.client.get('/vozdushnyi-filtr-151000079aa/')
        self.assertEqual(new_air.status_code, 200)
        self.assertContains(new_air, 'Tiggo 8 Pro 1.6')
        self.assertNotContains(new_air, '<li>Tiggo 7 Pro</li>')
        self.assertContains(new_air, 'https://zpt.kz/vozdushnyi-filtr-151000079aa/')

        old_spark = self.client.get('/chery-tiggo-7-f4j163707010-chery-tiggo-7/')
        self.assertEqual(old_spark.status_code, 301)
        cabin = self.client.get(reverse('product_detail', kwargs={'slug': self.cabin199.slug}))
        self.assertNotContains(cabin, '<li>Tiggo 8 Pro</li>')
        t218_page = self.client.get(reverse('product_detail', kwargs={'slug': self.t218.slug}))
        self.assertContains(t218_page, '<li>Tiggo 8 Pro</li>')

    def test_old_slugs_do_not_appear_in_public_links(self):
        self.assertEqual(self.air079.slug, 'chery-tiggo-7-pro-151000079aa-chery-tiggo-7-pro')
        self.assertEqual(self.air079.get_absolute_url(), '/vozdushnyi-filtr-151000079aa/')
        self.assertEqual(self.spark.get_absolute_url(), '/svecha-f4j163707010/')
        self.assertEqual(self.oil480.get_absolute_url(), '/masljanyi-filtr-4801012010/')
        self.assertEqual(self.cabin199.get_absolute_url(), '/salonnyi-filtr-301001199aa/')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '02', stdout=out)
        self.oil480.refresh_from_db()
        self.assertEqual(self.oil480.slug, 'changan-cs55')
        self.assertIn('dry-run', out.getvalue())
