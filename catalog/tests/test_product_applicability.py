from django.test import TestCase
from django.urls import reverse

from catalog.applicability import (
    VIN_WARNING,
    build_product_applicability,
    parse_plain_list,
    serialize_plain_list,
    title_contains_vehicle,
    vehicle_line_if_not_in_title,
)
from catalog.models import Brand, CarModel, Country, Product


def _make_product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77771234567',
        'status': 'active',
        'article': 'TEST-APP',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class ProductApplicabilityTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай APP')
        self.chery = Brand.objects.create(country=country, name='Chery')
        self.exeed = Brand.objects.create(country=country, name='Exeed')
        self.jetour = Brand.objects.create(country=country, name='Jetour')
        self.tiggo7 = CarModel.objects.create(brand=self.chery, name='Tiggo 7')
        self.tiggo8_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 8 Pro')
        self.txl = CarModel.objects.create(brand=self.exeed, name='TXL')
        self.dashing = CarModel.objects.create(brand=self.jetour, name='Dashing')

    def test_brand_model_not_repeated_when_already_in_title(self):
        product = _make_product(
            title='Масляный фильтр Chery Tiggo 8 Pro — F4J161012030',
            slug='oil-filter-tiggo8pro-app',
            article='F4J161012030-APP',
            brand=self.chery,
            car_model=self.tiggo8_pro,
        )
        self.assertTrue(title_contains_vehicle(product))
        self.assertEqual(vehicle_line_if_not_in_title(product), '')
        response = self.client.get(reverse('product_detail', kwargs={'slug': product.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, product.title)
        self.assertNotContains(response, 'product-vehicle-line')
        self.assertContains(response, 'Марка')
        self.assertContains(response, 'Chery')

    def test_vehicle_line_shown_when_title_has_no_brand_model(self):
        product = _make_product(
            title='Свеча зажигания F4J163707010',
            slug='spark-short-title-app',
            article='F4J163707010-APP',
            brand=self.chery,
            car_model=self.tiggo7,
        )
        self.assertEqual(vehicle_line_if_not_in_title(product), 'Chery Tiggo 7')
        response = self.client.get(reverse('product_detail', kwargs={'slug': product.slug}))
        self.assertContains(response, 'product-vehicle-line')
        self.assertContains(response, 'Chery Tiggo 7')
        self.assertContains(response, product.title)

    def test_primary_and_additional_models_grouped_without_duplicates(self):
        product = _make_product(
            title='Масляный фильтр Chery Tiggo 8 Pro — F4J161012030',
            slug='oil-grouped-app',
            article='F4J161012030-GRP',
            brand=self.chery,
            car_model=self.tiggo8_pro,
        )
        product.selected_brands.add(self.chery, self.exeed, self.jetour)
        product.selected_models.add(self.tiggo8_pro, self.txl, self.dashing)

        payload = build_product_applicability(product)
        brand_names = [group['brand'].name for group in payload['groups']]
        self.assertEqual(brand_names, ['Chery', 'Exeed', 'Jetour'])
        chery_models = [model.name for model in payload['groups'][0]['models']]
        self.assertEqual(chery_models, ['Tiggo 8 Pro'])
        self.assertEqual(
            [model.name for model in payload['groups'][1]['models']],
            ['TXL'],
        )
        self.assertEqual(
            [model.name for model in payload['groups'][2]['models']],
            ['Dashing'],
        )

        response = self.client.get(reverse('product_detail', kwargs={'slug': product.slug}))
        self.assertContains(response, 'Применяемость')
        self.assertContains(response, 'Tiggo 8 Pro')
        self.assertContains(response, 'TXL')
        self.assertContains(response, 'Dashing')
        self.assertContains(response, 'Exeed')
        self.assertContains(response, 'Jetour')
        html = response.content.decode()
        self.assertGreaterEqual(html.count('Tiggo 8 Pro'), 1)
        self.assertIn('Применяемость', html)

    def test_engines_and_oem_displayed(self):
        product = _make_product(
            title='Свеча зажигания Chery Tiggo 7 — F4J163707010',
            slug='spark-engines-oem',
            article='F4J163707010-OEM',
            brand=self.chery,
            car_model=self.tiggo7,
            engine_compatibility='1.5 Turbo; 1.6 TGDI; 1.5 Turbo',
            oem_cross_references='F4J163707010; F4J16-3707010; OE208',
        )
        response = self.client.get(reverse('product_detail', kwargs={'slug': product.slug}))
        self.assertContains(response, 'Двигатели:')
        self.assertContains(response, '1.5 Turbo')
        self.assertContains(response, '1.6 TGDI')
        self.assertContains(response, 'OEM / кросс-номера:')
        self.assertContains(response, 'F4J16-3707010')
        self.assertContains(response, 'OE208')
        self.assertContains(response, VIN_WARNING)

    def test_empty_engines_and_oem_do_not_render_headings(self):
        product = _make_product(
            title='Салонный фильтр Zeekr 001 — 8890649934',
            slug='cabin-empty-engines',
            article='8890649934-APP',
            brand=self.chery,
            car_model=self.tiggo7,
            engine_compatibility='',
            oem_cross_references='',
        )
        response = self.client.get(reverse('product_detail', kwargs={'slug': product.slug}))
        self.assertNotContains(response, 'Двигатели:')
        self.assertNotContains(response, 'OEM / кросс-номера:')
        self.assertContains(response, VIN_WARNING)

    def test_vin_warning_with_compatibility_only(self):
        product = _make_product(
            title='Фильтр без структуры',
            slug='compat-only-app',
            article='COMPAT-ONLY',
            compatibility='Только текстовая применимость',
        )
        response = self.client.get(reverse('product_detail', kwargs={'slug': product.slug}))
        self.assertContains(response, 'Применяемость')
        self.assertContains(response, 'Только текстовая применимость')
        self.assertNotContains(response, 'Подходит для')
        self.assertContains(response, VIN_WARNING)

    def test_duplicate_compatibility_is_not_repeated(self):
        product = _make_product(
            title='Масляный фильтр Chery — 4801012010',
            slug='oil-dup-compat',
            article='4801012010-DUP',
            brand=self.chery,
            car_model=self.tiggo7,
            compatibility='Chery Tiggo 4, Tiggo 7, Tiggo 7 Pro, Tiggo 8.',
        )
        tiggo4 = CarModel.objects.create(brand=self.chery, name='Tiggo 4')
        tiggo7_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro')
        tiggo8 = CarModel.objects.create(brand=self.chery, name='Tiggo 8')
        product.selected_models.add(self.tiggo7, tiggo4, tiggo7_pro, tiggo8)
        payload = build_product_applicability(product)
        self.assertEqual(payload['extra_compatibility'], '')
        response = self.client.get(reverse('product_detail', kwargs={'slug': product.slug}))
        self.assertContains(response, 'Применяемость')
        self.assertNotContains(response, 'Дополнительная применяемость')

    def test_wingle6_stays_visible_when_only_wingle7_is_structured(self):
        country = Country.objects.get(name='Китай APP')
        great_wall = Brand.objects.create(country=country, name='Great Wall')
        wingle7 = CarModel.objects.create(brand=great_wall, name='Wingle 7')
        product = _make_product(
            title='Салонный фильтр Great Wall Wingle 7 — 8104400XP24BA',
            slug='cabin-wingle7-app',
            article='8104400XP24BA-APP',
            brand=great_wall,
            car_model=wingle7,
            compatibility='Great Wall Wingle 7 (с 10.2018, 2.0), Wingle 6 (2014–2021, 2.4).',
        )
        product.selected_models.add(wingle7)
        payload = build_product_applicability(product)
        self.assertIn('Wingle 6', payload['extra_compatibility'])
        self.assertEqual(payload['extra_compatibility'], product.compatibility)
        response = self.client.get(reverse('product_detail', kwargs={'slug': product.slug}))
        self.assertContains(response, 'Применяемость')
        self.assertContains(response, 'Wingle 7')
        self.assertContains(response, 'Wingle 6')
        self.assertContains(response, 'Дополнительная применяемость')
        self.assertEqual(product.compatibility, 'Great Wall Wingle 7 (с 10.2018, 2.0), Wingle 6 (2014–2021, 2.4).')

    def test_pilot_fitment_keeps_extra_compatibility_facts(self):
        country = Country.objects.get(name='Китай APP')
        zeekr = Brand.objects.create(country=country, name='Zeekr')
        jetour_x70 = CarModel.objects.create(brand=self.jetour, name='X70')
        jetour_x90 = CarModel.objects.create(brand=self.jetour, name='X90')
        zeekr_001 = CarModel.objects.create(brand=zeekr, name='001')
        zeekr_009 = CarModel.objects.create(brand=zeekr, name='009')
        tiggo4 = CarModel.objects.create(brand=self.chery, name='Tiggo 4')
        tiggo7_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro')
        tiggo8 = CarModel.objects.create(brand=self.chery, name='Tiggo 8')
        great_wall = Brand.objects.create(country=country, name='Great Wall')
        wingle7 = CarModel.objects.create(brand=great_wall, name='Wingle 7')

        pilots = [
            {
                'article': 'F4J161012030',
                'title': 'Масляный фильтр Chery Tiggo 8 Pro / Exeed TXL — F4J161012030',
                'brand': self.chery,
                'model': self.tiggo8_pro,
                'selected': [self.tiggo8_pro, self.txl, self.dashing],
                'compatibility': 'Chery Tiggo 8 Pro 1.6, Exeed TXL 1.6, Jetour Dashing 1.6.',
                'expect_extra': True,
                'must_contain': ['Tiggo 8 Pro', 'TXL', 'Dashing', '1.6'],
            },
            {
                'article': 'F4J163707010',
                'title': 'Свеча зажигания Chery Tiggo 7 — F4J163707010',
                'brand': self.chery,
                'model': self.tiggo7,
                'selected': [self.tiggo7, jetour_x70, jetour_x90],
                'compatibility': 'Chery Tiggo 7 (1.5T / 1.6T), Jetour X70, Jetour X90.',
                'expect_extra': True,
                'must_contain': ['Tiggo 7', 'X70', 'X90', '1.5T'],
            },
            {
                'article': '4801012010',
                'title': 'Масляный фильтр Chery Tiggo 4 / Tiggo 7 / Tiggo 8 — 4801012010',
                'brand': self.chery,
                'model': self.tiggo7,
                'selected': [tiggo4, self.tiggo7, tiggo7_pro, tiggo8],
                'compatibility': 'Chery Tiggo 4, Tiggo 7, Tiggo 7 Pro, Tiggo 8.',
                'expect_extra': False,
                'must_contain': ['Tiggo 4', 'Tiggo 7', 'Tiggo 7 Pro', 'Tiggo 8'],
            },
            {
                'article': '8890649934',
                'title': 'Салонный фильтр Zeekr 001 / 009 — 8890649934',
                'brand': zeekr,
                'model': zeekr_001,
                'selected': [zeekr_001, zeekr_009],
                'compatibility': 'Zeekr 001, Zeekr 009.',
                'expect_extra': False,
                'must_contain': ['001', '009'],
            },
            {
                'article': '8104400XP24BA',
                'title': 'Салонный фильтр Great Wall Wingle 7 — 8104400XP24BA',
                'brand': great_wall,
                'model': wingle7,
                'selected': [wingle7],
                'compatibility': 'Great Wall Wingle 7 (с 10.2018, 2.0), Wingle 6 (2014–2021, 2.4).',
                'expect_extra': True,
                'must_contain': ['Wingle 7', 'Wingle 6'],
            },
        ]

        for spec in pilots:
            with self.subTest(article=spec['article']):
                product = _make_product(
                    title=spec['title'],
                    slug=f"pilot-{spec['article'].lower()}",
                    article=spec['article'],
                    brand=spec['brand'],
                    car_model=spec['model'],
                    compatibility=spec['compatibility'],
                )
                product.selected_models.set(spec['selected'])
                payload = build_product_applicability(product)
                if spec['expect_extra']:
                    self.assertEqual(payload['extra_compatibility'], spec['compatibility'])
                else:
                    self.assertEqual(payload['extra_compatibility'], '')
                self.assertEqual(product.compatibility, spec['compatibility'])
                response = self.client.get(
                    reverse('product_detail', kwargs={'slug': product.slug})
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Применяемость')
                for fragment in spec['must_contain']:
                    self.assertContains(response, fragment)
                self.assertNotContains(response, '<h2>Подходит для</h2>', html=False)

    def test_parse_plain_list_drops_html_urls_and_duplicates(self):
        raw = (
            'F4J163707010; <b>F4J16-3707010</b>; '
            'https://ozon.ru/item; F4J163707010; OE208'
        )
        self.assertEqual(
            parse_plain_list(raw),
            ['F4J163707010', 'F4J16-3707010', 'OE208'],
        )
        self.assertEqual(
            serialize_plain_list(raw),
            'F4J163707010\nF4J16-3707010\nOE208',
        )

    def test_hyundai_kia_full_compatibility_is_visible_on_public_card(self):
        korea = Country.objects.create(name='Корея APP')
        hyundai = Brand.objects.create(country=korea, name='Hyundai')
        kia = Brand.objects.create(country=korea, name='Kia')
        elantra = CarModel.objects.create(brand=hyundai, name='Elantra')
        tucson = CarModel.objects.create(brand=hyundai, name='Tucson')
        santa_fe = CarModel.objects.create(brand=hyundai, name='Santa Fe')
        CarModel.objects.create(brand=kia, name='Sportage')
        compatibility = (
            'Hyundai Accent, Elantra, Tucson, Santa Fe; '
            'Kia Soul, Forte, Sportage, Sorento, Niro'
        )
        product = _make_product(
            title='Масляный фильтр Hyundai/Kia 26300-35505',
            slug='oil-hyundai-kia-2142-app',
            article='26300-35505-APP',
            brand=hyundai,
            car_model=elantra,
            compatibility=compatibility,
        )
        product.selected_models.add(santa_fe, tucson)
        payload = build_product_applicability(product)
        hyundai_group = next(group for group in payload['groups'] if group['brand'].pk == hyundai.pk)
        self.assertEqual(
            [model.name for model in hyundai_group['models']],
            ['Elantra', 'Santa Fe', 'Tucson'],
        )
        self.assertIn('Kia', payload['extra_compatibility'])
        self.assertIn('Sportage', payload['extra_compatibility'])
        self.assertEqual(payload['extra_compatibility'], compatibility)
        response = self.client.get(reverse('product_detail', kwargs={'slug': product.slug}))
        self.assertContains(response, 'Применяемость')
        self.assertContains(response, 'Hyundai')
        self.assertContains(response, 'Elantra')
        self.assertContains(response, 'Santa Fe')
        self.assertContains(response, 'Tucson')
        self.assertContains(response, 'Дополнительная применяемость')
        self.assertContains(response, 'Kia Soul')
        self.assertContains(response, 'Sportage')
        self.assertContains(response, 'Niro')
        self.assertNotContains(response, 'research_notes')
        self.assertNotContains(response, 'ChatGPT')
        self.assertNotContains(response, 'generated by AI')
        self.assertNotContains(response, 'по данным поставщика')
        self.assertEqual(product.compatibility, compatibility)
        self.assertEqual(CarModel.objects.filter(brand=kia, name='Soul').count(), 0)
