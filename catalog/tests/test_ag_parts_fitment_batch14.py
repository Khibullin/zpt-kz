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
        'article': 'FIT-B14',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch14Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B14')
        self.exeed = Brand.objects.create(country=country, name='Exeed')
        self.jac = Brand.objects.create(country=country, name='JAC')
        self.lifan = Brand.objects.create(country=country, name='Lifan')
        self.gwm = Brand.objects.create(country=country, name='Great Wall')
        self.changan = Brand.objects.create(country=country, name='Changan')
        self.byd = Brand.objects.create(country=country, name='BYD')
        self.zeekr = Brand.objects.create(country=country, name='Zeekr')
        self.li = Brand.objects.create(country=country, name='Li Auto')
        self.txl = CarModel.objects.create(brand=self.exeed, name='TXL')
        self.vx = CarModel.objects.create(brand=self.exeed, name='VX')
        self.s5 = CarModel.objects.create(brand=self.jac, name='S5')
        self.unik = CarModel.objects.create(brand=self.changan, name='UNI-K')
        self.zx = CarModel.objects.create(brand=self.zeekr, name='X')
        self.z001 = CarModel.objects.create(brand=self.zeekr, name='001')
        self.z009 = CarModel.objects.create(brand=self.zeekr, name='009')
        self.l6 = CarModel.objects.create(brand=self.li, name='L6')
        self.l7 = CarModel.objects.create(brand=self.li, name='L7')
        self.l8 = CarModel.objects.create(brand=self.li, name='L8')
        self.l9 = CarModel.objects.create(brand=self.li, name='L9')
        self.poer = CarModel.objects.create(brand=self.gwm, name='Poer')

        self.cabin199 = _product(
            article='301001199AA',
            title='Салонный фильтр EXEED TXL / VX — 301001199AA',
            slug='salonnyi-filtr-301001199aa',
            brand=self.exeed,
            car_model=self.txl,
            price=1532,
            stock_qty=11,
            compatibility='EXEED TXL, EXEED VX. Угольный OEM 301001199AA.',
            oem_cross_references='301001199AA\n301001199A',
        )
        self.cabin199.selected_models.add(self.txl, self.vx)
        ProductPriceTier.objects.create(product=self.cabin199, min_qty=1, price=1532)

        self.cabin265 = _product(
            article='301000265AA',
            title='Салонный фильтр Exeed TXL — 301000265AA',
            slug='salonnyi-filtr-301000265aa',
            brand=self.exeed,
            car_model=self.txl,
            price=1529,
            stock_qty=8,
            compatibility='Exeed TXL.',
            oem_cross_references='301000265AA',
        )
        self.cabin265.selected_models.add(self.txl, self.vx)
        ProductPriceTier.objects.create(product=self.cabin265, min_qty=1, price=1529)

        self.jac_s5 = _product(
            article='1109130U1510',
            title='Воздушный фильтр JAC S5 — 1109130U1510',
            slug='jac-s5-1109130u1510',
            brand=self.jac,
            car_model=self.s5,
            price=2099,
            stock_qty=6,
            compatibility='JAC S5',
            oem_cross_references='28113-2S000',
        )
        self.jac_s5.selected_models.add(self.s5)

        self.jac_n = _product(
            article='1109140W5000',
            title='Воздушный фильтр 1109140W5000',
            slug='1109140w5000',
            brand=self.jac,
            price=4180,
            stock_qty=4,
            compatibility='JAC N25, N35 (E5); Sollers Argo',
            oem_cross_references='28113-4F000\n1109140W5000-AM001',
        )

        self.fae = _product(
            article='FAE1109160',
            title='Воздушный фильтр FAE1109160',
            slug='fae1109160',
            brand=self.lifan,
            price=1760,
            stock_qty=10,
            compatibility='LIFAN 320, 330, Smily',
            oem_cross_references='FAE1109120',
        )

        self.xp6 = _product(
            article='1109110XP6EXACHS',
            title='Воздушный фильтр 1109110XP6EXACHS',
            slug='1109110xp6exachs',
            brand=self.gwm,
            price=2640,
            stock_qty=7,
            compatibility='GWM Poer, 2024–2025 — 2.0T',
            engine_compatibility='2.0T',
            oem_cross_references='1109110XP6EXACHS',
        )
        self.xp6.selected_models.add(self.poer)

        self.cd569 = _product(
            article='CD569F2801032700',
            title='Салонный фильтр Changan UNI-K — CD569F2801032700',
            slug='changan-uni-k-changan',
            brand=self.changan,
            car_model=self.unik,
            price=1839,
            stock_qty=5,
            compatibility='Changan UNI-K',
            oem_cross_references='CD569F2801032700\nCD569F280103-2700',
        )
        self.cd569.selected_models.add(self.unik)

        self.em2e = _product(
            article='EM2E-8121211E',
            title='Салонный фильтр BYD — EM2E-8121211E',
            slug='byd-em2e-8121211e',
            brand=self.byd,
            price=4290,
            stock_qty=3,
            compatibility='BYD Dolphin, BYD Atto 3 (Yuan Plus).',
            oem_cross_references='EM2E-8121211E\nEM2E8121211E',
        )

        self.rf059 = _product(
            article='RF059ZKR',
            title='Салонный фильтр Zeekr X — RF059ZKR',
            slug='zeekr-x-rf059zkr-zeekr-x',
            brand=self.zeekr,
            car_model=self.zx,
            price=1760,
            stock_qty=4,
            compatibility='Zeekr X',
            oem_cross_references='RF059ZKR\n8894900181',
        )
        self.rf059.selected_models.add(self.zx)

        self.z889 = _product(
            article='8890649934',
            title='Салонный фильтр Zeekr 001 — 8890649934',
            slug='zeekr-001-8890649934-zeekr-001',
            brand=self.zeekr,
            car_model=self.z001,
            price=3839,
            stock_qty=2,
            compatibility='Zeekr 001 и Zeekr 009',
            oem_cross_references='8890649934',
        )
        self.z889.selected_models.add(self.z001, self.z009)

        self.x039 = _product(
            article='X0390000206',
            title='Салонный фильтр Li Auto L7 — X0390000206',
            slug='li-auto-l7-x0390000206-li-auto-l7',
            brand=self.li,
            car_model=self.l7,
            price=3034,
            stock_qty=3,
            compatibility='Li Auto L6 и L7. L8 и L9 не входят: для них OEM X01-90000044.',
            oem_cross_references='X0390000206',
        )
        self.x039.selected_models.add(self.l6, self.l7)

        self.x01 = _product(
            article='X01-90000014',
            title='Воздушный фильтр Li Auto L7 — X01-90000014',
            slug='li-auto-l7-x01-90000014-li-auto-l7',
            brand=self.li,
            car_model=self.l7,
            price=985,
            stock_qty=6,
            compatibility='Li Auto L6, L7, L8, L9. OEM X01-90000014.',
            oem_cross_references='X01-90000014\nX0190000014',
        )
        self.x01.selected_models.add(self.l6, self.l7, self.l8, self.l9)

    def test_batch_14_has_no_research_notes(self):
        spec = load_batch('14')
        self.assertEqual(len(spec['articles']), 12)
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_14_engines_oem_unmix_and_honest_models(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('14')), apply=True)

        self.cabin199.refresh_from_db()
        self.assertEqual(self.cabin199.price, 1532)
        self.assertIn('SQRF4J16A', self.cabin199.engine_compatibility)
        self.assertIn('SQRF4J20C', self.cabin199.engine_compatibility)
        self.assertNotIn('301000260AA', self.cabin199.oem_cross_references)
        self.assertEqual(
            set(self.cabin199.selected_models.values_list('name', flat=True)),
            {'TXL', 'VX'},
        )
        page199 = self.client.get('/salonnyi-filtr-301001199aa/')
        self.assertEqual(page199.status_code, 200)
        self.assertContains(page199, 'SQRF4J16A')
        self.assertContains(page199, '301000265AA')
        self.assertContains(page199, 'T21-8107011')
        self.assertNotContains(page199, '301000260AA')

        self.cabin265.refresh_from_db()
        self.assertEqual(self.cabin265.price, 1529)
        self.assertIn('TXL / VX', self.cabin265.title)
        self.assertIn('SQRF4J16A', self.cabin265.engine_compatibility)
        self.assertIn('301001199AA', self.cabin265.description)
        self.assertEqual(
            set(self.cabin265.selected_models.values_list('name', flat=True)),
            {'TXL', 'VX'},
        )

        self.jac_s5.refresh_from_db()
        self.assertEqual(self.jac_s5.price, 2099)
        self.assertNotIn('28113-2S000', self.jac_s5.oem_cross_references)
        self.assertIn('1109130U1510', self.jac_s5.oem_cross_references)
        self.assertFalse(self.jac_s5.selected_models.exists())
        self.assertIsNone(self.jac_s5.car_model_id)
        s5_page = self.client.get('/jac-s5-1109130u1510/')
        self.assertEqual(s5_page.status_code, 200)
        self.assertContains(s5_page, 'не подтверждаем')
        self.assertContains(s5_page, 'Не смешивать с 28113-2S000')
        self.assertNotIn('28113-2S000', self.jac_s5.oem_cross_references)

        self.jac_n.refresh_from_db()
        self.assertNotIn('28113-4F000', self.jac_n.oem_cross_references)
        self.assertNotIn('AM001', self.jac_n.oem_cross_references)
        self.assertEqual(self.jac_n.oem_cross_references.strip(), '1109140W5000')

        self.fae.refresh_from_db()
        self.assertEqual(self.fae.price, 1760)
        self.assertEqual(self.fae.oem_cross_references.strip(), 'FAE1109160')
        self.assertNotIn('FAE1109120', self.fae.oem_cross_references)
        fae_page = self.client.get('/fae1109160/')
        self.assertEqual(fae_page.status_code, 200)
        self.assertContains(fae_page, 'Не смешивать с FAE1109120')
        self.assertNotIn('FAE1109120', self.fae.oem_cross_references)

        self.xp6.refresh_from_db()
        self.assertEqual(self.xp6.engine_compatibility, '')
        self.assertFalse(self.xp6.selected_models.exists())
        self.assertIn('не подтверждаем', self.xp6.compatibility)

        self.cd569.refresh_from_db()
        self.assertFalse(self.cd569.selected_models.exists())
        self.assertIsNone(self.cd569.car_model_id)
        self.assertNotIn('UNI-K —', self.cd569.title)
        cd_page = self.client.get('/changan-uni-k-changan/')
        self.assertEqual(cd_page.status_code, 200)
        self.assertContains(cd_page, 'не подтверждаем')
        self.assertNotContains(cd_page, '<li>UNI-K</li>')

        self.em2e.refresh_from_db()
        self.assertIn('не подтверждаем', self.em2e.compatibility)
        self.assertNotIn('Основная применимость: BYD Dolphin', self.em2e.description)

        self.rf059.refresh_from_db()
        self.assertEqual(self.rf059.oem_cross_references.strip(), 'RF059ZKR')
        self.assertNotIn('8894900181', self.rf059.oem_cross_references)
        self.assertFalse(self.rf059.selected_models.exists())

        self.z889.refresh_from_db()
        self.assertFalse(self.z889.selected_models.exists())
        self.assertIsNone(self.z889.car_model_id)
        self.assertIn('не подтверждаем', self.z889.compatibility)

        self.x039.refresh_from_db()
        self.assertFalse(self.x039.selected_models.exists())
        self.assertNotIn('не входят', self.x039.compatibility)
        self.assertNotIn('не входят', self.x039.description)
        x039_page = self.client.get('/li-auto-l7-x0390000206-li-auto-l7/')
        self.assertEqual(x039_page.status_code, 200)
        self.assertNotContains(x039_page, 'не входят')

        self.x01.refresh_from_db()
        self.assertFalse(self.x01.selected_models.exists())
        self.assertNotIn('29150063', self.x01.oem_cross_references)
        self.assertIn('не подтверждаем', self.x01.compatibility)

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '14', stdout=out)
        self.jac_s5.refresh_from_db()
        self.assertEqual(self.jac_s5.oem_cross_references, '28113-2S000')
        self.assertIn('dry-run', out.getvalue())
