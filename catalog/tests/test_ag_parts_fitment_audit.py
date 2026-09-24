from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from catalog.ag_parts_fitment_audit import (
    STATUS_CHANGED,
    STATUS_DUPLICATE,
    STATUS_ERROR,
    STATUS_UNCHANGED,
    STATUS_WOULD_CHANGE,
    apply_fitment_plans,
    load_batch,
    plan_fitment_batch,
)
from catalog.models import Brand, CarModel, Country, Product, ProductPriceTier
from catalog.product_quality import detect_internal_research_text


def _country():
    return Country.objects.create(name='Китай FIT')


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'FIT-TEST',
        'stock_qty': 12,
        'cost_price': 500,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentAuditTests(TestCase):
    def setUp(self):
        country = _country()
        self.chery = Brand.objects.create(country=country, name='Chery')
        self.exeed = Brand.objects.create(country=country, name='Exeed')
        self.jaecoo = Brand.objects.create(country=country, name='Jaecoo')
        self.omoda = Brand.objects.create(country=country, name='Omoda')
        self.haval = Brand.objects.create(country=country, name='Haval')
        self.jetour = Brand.objects.create(country=country, name='Jetour')
        self.tiggo7 = CarModel.objects.create(brand=self.chery, name='Tiggo 7')
        self.tiggo7_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro')
        self.tiggo7_pro_max = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro Max')
        self.tiggo8 = CarModel.objects.create(brand=self.chery, name='Tiggo 8')
        self.tiggo8_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 8 Pro')
        self.arrizo8 = CarModel.objects.create(brand=self.chery, name='Arrizo 8')
        self.txl = CarModel.objects.create(brand=self.exeed, name='TXL')
        self.lx = CarModel.objects.create(brand=self.exeed, name='LX')
        self.j7 = CarModel.objects.create(brand=self.jaecoo, name='J7')
        self.c5 = CarModel.objects.create(brand=self.omoda, name='C5')
        self.dargo = CarModel.objects.create(brand=self.haval, name='Dargo')
        self.f7 = CarModel.objects.create(brand=self.haval, name='F7')
        self.f7x = CarModel.objects.create(brand=self.haval, name='F7x')
        self.h9 = CarModel.objects.create(brand=self.haval, name='H9')
        self.x70 = CarModel.objects.create(brand=self.jetour, name='X70')
        self.x90 = CarModel.objects.create(brand=self.jetour, name='X90')
        self.dashing = CarModel.objects.create(brand=self.jetour, name='Dashing')

        self.air079 = _product(
            article='151000079AA',
            title='Воздушный фильтр Chery Tiggo 7 Pro — 151000079AA',
            slug='chery-tiggo-7-pro-151000079aa-chery-tiggo-7-pro',
            brand=self.chery,
            car_model=self.tiggo7_pro,
            price=1000,
            stock_qty=8,
            compatibility='Chery Tiggo 7 Pro / 7 Pro Max, Tiggo 8 Pro, Arrizo 8',
            description='Основная применимость: Chery Tiggo 7 Pro / 7 Pro Max.',
        )
        self.air079.selected_models.add(
            self.tiggo7_pro, self.tiggo7_pro_max, self.tiggo8_pro, self.arrizo8, self.lx, self.j7, self.c5,
        )
        ProductPriceTier.objects.create(product=self.air079, min_qty=1, price=1000)

        self.spark = _product(
            article='F4J163707010',
            title='Свеча зажигания Chery Tiggo 7 — F4J163707010',
            slug='chery-tiggo-7-f4j163707010-chery-tiggo-7',
            brand=self.chery,
            car_model=self.tiggo7,
            price=2450,
            stock_qty=20,
            description='Chery Tiggo 7, Jetour X70 и Jetour X90',
        )
        self.spark.selected_models.add(self.tiggo7, self.x70, self.x90)

        self.oil_dargo = _product(
            article='1017110XEN01',
            title='Масляный фильтр Haval Dargo — 1017110XEN01',
            slug='haval-dargo-1017110xen01-haval-dargo',
            brand=self.haval,
            car_model=self.dargo,
            price=2066,
            stock_qty=5,
            compatibility='Haval Dargo, F7, F7x, H9; Great Wall Poer; Tank 500. Двигатель 2.0T GW4N20.',
            description='Основная применимость: Haval Dargo, F7, F7x, H9.',
        )
        self.oil_dargo.selected_models.add(self.dargo, self.f7, self.f7x, self.h9)

        self.m111 = _product(
            article='M111109111',
            title='Воздушный фильтр M111109111',
            slug='m111109111',
            brand=self.chery,
            price=1540,
            stock_qty=4,
            compatibility='Chery A3, Chery Chance M11',
            description='Применимость: Chery A3, Chery Chance M11.',
        )

        self.oil_f4j16 = _product(
            article='F4J161012030',
            title='Масляный фильтр Chery Tiggo 8 Pro — F4J161012030',
            slug='chery-tiggo-8-pro-f4j161012030-chery-tiggo-8-pro',
            brand=self.chery,
            car_model=self.tiggo8_pro,
            price=1950,
            stock_qty=6,
            description='Chery Tiggo 8 Pro 1.6, Exeed TXL 1.6, Jetour Dashing 1.6.',
        )
        self.oil_f4j16.selected_models.add(self.tiggo8_pro, self.txl, self.dashing)

    def test_batch_01_file_has_no_internal_research_text(self):
        spec = load_batch('01')
        self.assertEqual(spec['batch_id'], '01')
        for row in spec['articles']:
            fields = row['fields']
            for name, value in fields.items():
                self.assertFalse(
                    detect_internal_research_text(value),
                    f'{row["article"]} {name} содержит служебный текст',
                )

    def test_dry_run_does_not_write(self):
        spec = load_batch('01')
        plans = plan_fitment_batch(spec)
        apply_fitment_plans(plans, apply=False)
        self.air079.refresh_from_db()
        self.assertEqual(self.air079.title, 'Воздушный фильтр Chery Tiggo 7 Pro — 151000079AA')
        self.assertEqual(self.air079.car_model_id, self.tiggo7_pro.pk)
        self.assertIn(self.tiggo7_pro, list(self.air079.selected_models.all()))
        self.assertTrue(any(item.status == STATUS_WOULD_CHANGE for item in plans))

    def test_apply_removes_false_fitment_and_keeps_price_stock_pp(self):
        spec = load_batch('01')
        plans = plan_fitment_batch(spec)
        apply_fitment_plans(plans, apply=True)
        statuses = {item.article: item.status for item in plans}
        self.assertEqual(statuses['151000079AA'], STATUS_CHANGED)
        self.assertEqual(statuses['F4J163707010'], STATUS_CHANGED)
        self.assertEqual(statuses['1017110XEN01'], STATUS_CHANGED)
        self.assertEqual(statuses['M111109111'], STATUS_CHANGED)
        self.assertEqual(statuses['F4J161012030'], STATUS_CHANGED)

        self.air079.refresh_from_db()
        air_models = set(self.air079.selected_models.values_list('name', flat=True))
        self.assertNotIn('Tiggo 7 Pro', air_models)
        self.assertEqual(self.air079.car_model_id, self.tiggo8_pro.pk)
        self.assertIn('Tiggo 8 Pro 1.6', self.air079.title)
        self.assertNotIn('Tiggo 7 Pro 1.5T', self.air079.title)
        self.assertEqual(self.air079.price, 1000)
        self.assertEqual(self.air079.stock_qty, 8)
        self.assertEqual(self.air079.slug, 'chery-tiggo-7-pro-151000079aa-chery-tiggo-7-pro')
        self.assertEqual(self.air079.article, '151000079AA')
        self.assertEqual(self.air079.cost_price, 500)
        self.assertEqual(self.air079.price_tiers.get().price, 1000)

        self.spark.refresh_from_db()
        spark_models = set(self.spark.selected_models.values_list('name', flat=True))
        self.assertNotIn('Tiggo 7', spark_models)
        self.assertNotIn('X70', spark_models)
        self.assertNotIn('X90', spark_models)
        self.assertIn('Tiggo 8', spark_models)
        self.assertIn('TXL', spark_models)
        self.assertEqual(self.spark.car_model_id, self.tiggo8.pk)
        self.assertEqual(self.spark.price, 2450)
        self.assertEqual(self.spark.stock_qty, 20)

        self.oil_dargo.refresh_from_db()
        oil_models = set(self.oil_dargo.selected_models.values_list('name', flat=True))
        self.assertNotIn('H9', oil_models)
        self.assertIn('Dargo', oil_models)
        self.assertIn('не H9 2.0T', self.oil_dargo.compatibility)

        self.m111.refresh_from_db()
        self.assertIn('A3 / M11', self.m111.title)
        self.assertIn('Не для Jetour X70', self.m111.compatibility)
        self.assertEqual(self.m111.selected_models.count(), 0)
        self.assertEqual(self.m111.slug, 'm111109111')

        self.oil_f4j16.refresh_from_db()
        self.assertIn('480-1012010', self.oil_f4j16.description)
        self.assertEqual(self.oil_f4j16.price, 1950)

    def test_public_cards_hide_false_models(self):
        spec = load_batch('01')
        apply_fitment_plans(plan_fitment_batch(spec), apply=True)
        air = self.client.get(reverse('product_detail', kwargs={'slug': self.air079.slug}))
        self.assertEqual(air.status_code, 200)
        self.assertContains(air, 'Tiggo 8 Pro')
        self.assertContains(air, 'Arrizo 8')
        self.assertNotContains(air, '<li>Tiggo 7 Pro</li>')

        spark = self.client.get(reverse('product_detail', kwargs={'slug': self.spark.slug}))
        self.assertEqual(spark.status_code, 200)
        self.assertContains(spark, 'Tiggo 8')
        self.assertNotContains(spark, '<li>Tiggo 7</li>')
        self.assertNotContains(spark, '<li>X70</li>')

        oil = self.client.get(reverse('product_detail', kwargs={'slug': self.oil_dargo.slug}))
        self.assertNotContains(oil, '<li>H9</li>')

    def test_duplicate_article_is_error(self):
        _product(article='151000079AA', title='Дубль', slug='dup-079')
        plans = plan_fitment_batch(load_batch('01'))
        by_article = {item.article: item for item in plans}
        self.assertEqual(by_article['151000079AA'].status, STATUS_DUPLICATE)
        apply_fitment_plans(plans, apply=True)
        self.air079.refresh_from_db()
        self.assertEqual(self.air079.car_model_id, self.tiggo7_pro.pk)

    def test_missing_model_blocks_only_that_row(self):
        spec = load_batch('01')
        self.j7.delete()
        plans = plan_fitment_batch(spec)
        by_article = {item.article: item for item in plans}
        self.assertEqual(by_article['151000079AA'].status, STATUS_ERROR)
        self.assertEqual(by_article['M111109111'].status, STATUS_WOULD_CHANGE)

    def test_second_apply_is_unchanged(self):
        spec = load_batch('01')
        apply_fitment_plans(plan_fitment_batch(spec), apply=True)
        plans = plan_fitment_batch(spec)
        self.assertTrue(all(item.status == STATUS_UNCHANGED for item in plans))

    def test_management_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '01', stdout=out)
        self.air079.refresh_from_db()
        self.assertIn('dry-run', out.getvalue())
        self.assertEqual(self.air079.car_model_id, self.tiggo7_pro.pk)
