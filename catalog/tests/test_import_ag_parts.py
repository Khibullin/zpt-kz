import zipfile
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from catalog.models import (
    Brand,
    CarModel,
    Category,
    Country,
    Product,
    ProductImage,
    SellerProfile,
)

MINIMAL_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
    b'\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\x0d\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82'
)

COST_MARKER = 87654321


def _write_xlsx(path, headers, rows, sheet_name='Прайс'):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


class AgPartsImportTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.user = User.objects.create_user(
            username='agparts',
            password='secret12345',
        )
        self.seller = SellerProfile.objects.create(
            user=self.user,
            name='AG Parts',
            phone='77771360740',
            city='Алматы',
        )
        self.country = Country.objects.create(name='Китай')
        self.chery = Brand.objects.create(country=self.country, name='Chery')
        self.haval = Brand.objects.create(country=self.country, name='Haval')
        self.tiggo7 = CarModel.objects.create(brand=self.chery, name='Tiggo 7')
        self.tiggo8 = CarModel.objects.create(brand=self.chery, name='Tiggo 8')
        self.jolion = CarModel.objects.create(brand=self.haval, name='Jolion')
        self.filters = Category.objects.create(name='Фильтры')
        self.electric = Category.objects.create(name='Электрика')
        self.price_xlsx = self.root / 'price.xlsx'
        self.cost_xlsx = self.root / 'cost.xlsx'
        self.photos_dir = self.root / 'photos'
        self.photos_dir.mkdir()
        self._write_default_sources()

    def _write_default_sources(self, extra_rows=None):
        rows = [
            [
                'CF-100',
                'CAIBIN FILTER',
                'Салонный фильтр CHERY',
                'CHERY',
                'Tiggo 7 / Tiggo 8',
                'CHERY Tiggo 7 / Tiggo 8',
                1200,
                10,
            ],
            [
                'AF-200',
                'AIR FILTER',
                'Воздушный фильтр Haval',
                'HAVAL',
                'Jolion',
                'HAVAL Jolion',
                1500,
                4,
            ],
        ]
        if extra_rows:
            rows.extend(extra_rows)
        _write_xlsx(
            self.price_xlsx,
            [
                'Артикул',
                'Категория',
                'Название',
                'Марка',
                'Модель',
                'Применимость',
                'Цена',
                'Кол-во',
            ],
            rows,
        )
        _write_xlsx(
            self.cost_xlsx,
            ['Артикул', 'Себестоимость'],
            [['CF-100', COST_MARKER]],
            sheet_name='Себест',
        )
        (self.photos_dir / 'CF-100.png').write_bytes(MINIMAL_PNG)
        (self.photos_dir / 'CF-100-2.png').write_bytes(
            MINIMAL_PNG + b'\x00'
        )
        (self.photos_dir / 'AF-200.png').write_bytes(MINIMAL_PNG)

    def _run(self, *args, **kwargs):
        stdout = StringIO()
        call_command(
            'import_ag_parts',
            f'--price-xlsx={self.price_xlsx}',
            f'--cost-xlsx={self.cost_xlsx}',
            f'--photos={self.photos_dir}',
            f'--seller-profile-id={self.seller.pk}',
            *args,
            stdout=stdout,
            **kwargs,
        )
        return stdout.getvalue()

    def test_dry_run_does_not_write(self):
        output = self._run('--dry-run')
        self.assertIn('mode: dry-run', output)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(ProductImage.objects.count(), 0)

    def test_new_article_creates_hidden_product(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(seller_profile=self.seller, article='CF-100')
        self.assertEqual(product.status, 'hidden')
        self.assertFalse(product.price_on_request)
        self.assertEqual(product.price, 1200)
        self.assertEqual(product.supplier, Product.SUPPLIER_LOCAL)

    def test_repeat_run_does_not_duplicate(self):
        self._run('--articles=CF-100')
        self._run('--articles=CF-100')
        self.assertEqual(
            Product.objects.filter(seller_profile=self.seller, article='CF-100').count(),
            1,
        )

    def test_other_seller_article_is_not_changed(self):
        other_user = User.objects.create_user(username='other', password='secret12345')
        other = SellerProfile.objects.create(
            user=other_user,
            name='Other Shop',
            phone='77770000001',
            city='Астана',
        )
        foreign = Product.objects.create(
            title='Чужой товар',
            article='CF-100',
            price=9999,
            seller_name=other.name,
            whatsapp_number=other.phone,
            status='active',
            seller_profile=other,
            city='Астана',
        )
        self._run('--articles=CF-100')
        foreign.refresh_from_db()
        self.assertEqual(foreign.title, 'Чужой товар')
        self.assertEqual(foreign.price, 9999)
        self.assertEqual(foreign.seller_profile, other)
        own = Product.objects.get(seller_profile=self.seller, article='CF-100')
        self.assertNotEqual(own.pk, foreign.pk)

    def test_cost_price_imported(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.cost_price, COST_MARKER)

    def test_missing_cost_price_warning(self):
        output = self._run('--dry-run', '--articles=AF-200')
        self.assertIn('missing_cost_price', output)

    def test_brand_normalized(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.brand, self.chery)

    def test_model_normalized(self):
        self._run('--articles=AF-200')
        product = Product.objects.get(article='AF-200', seller_profile=self.seller)
        self.assertEqual(product.car_model, self.jolion)
        self.assertEqual(product.brand, self.haval)

    def test_unknown_brand_warning(self):
        _write_xlsx(
            self.price_xlsx,
            ['Артикул', 'Категория', 'Марка', 'Модель', 'Применимость', 'Цена'],
            [['ZZ-1', 'AIR FILTER', 'NOTEXISTBRAND', '', 'NOTEXISTBRAND Foo', 100]],
        )
        output = self._run('--dry-run', '--articles=ZZ-1')
        self.assertIn('unknown_brand', output)

    def test_unknown_model_warning(self):
        _write_xlsx(
            self.price_xlsx,
            ['Артикул', 'Категория', 'Марка', 'Модель', 'Применимость', 'Цена'],
            [['ZZ-2', 'AIR FILTER', 'CHERY', 'UnknownX', 'CHERY UnknownX', 100]],
        )
        output = self._run('--dry-run', '--articles=ZZ-2')
        self.assertIn('unknown_model', output)

    def test_selected_models_created(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(
            set(product.selected_models.values_list('id', flat=True)),
            {self.tiggo7.id, self.tiggo8.id},
        )
        self.assertIn(self.chery, product.selected_brands.all())

    def test_compatibility_saved(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertIn('Tiggo 7', product.compatibility)
        self.assertIn('Tiggo 8', product.compatibility)

    def test_category_mapping(self):
        self._run('--articles=AF-200')
        product = Product.objects.get(article='AF-200', seller_profile=self.seller)
        self.assertEqual(product.category, self.filters)

    def test_caibin_typo_normalized(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.category, self.filters)

    def test_main_image_saved(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertTrue(product.main_image)
        self.assertIn('CF-100', product.main_image.name)

    def test_additional_images_saved(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.images.count(), 1)

    def test_repeat_run_does_not_duplicate_images(self):
        self._run('--articles=CF-100')
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.images.count(), 1)

    def test_replace_images(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        old_name = product.main_image.name
        replacement = self.photos_dir / 'CF-100.png'
        replacement.write_bytes(MINIMAL_PNG + b'\x01\x02')
        self._run('--articles=CF-100', '--replace-images')
        product.refresh_from_db()
        self.assertTrue(product.main_image)
        self.assertNotEqual(product.main_image.size, 0)
        self.assertEqual(product.images.count(), 1)

    def test_limit(self):
        self._run('--limit=1')
        self.assertEqual(Product.objects.filter(seller_profile=self.seller).count(), 1)

    def test_articles_filter(self):
        self._run('--articles=AF-200')
        self.assertTrue(
            Product.objects.filter(seller_profile=self.seller, article='AF-200').exists()
        )
        self.assertFalse(
            Product.objects.filter(seller_profile=self.seller, article='CF-100').exists()
        )

    def test_phaeton_product_not_changed(self):
        phaeton = Product.objects.create(
            title='Phaeton item',
            article='CF-100',
            price=1111,
            seller_name='Phaeton',
            whatsapp_number='77000000000',
            status='active',
            supplier=Product.SUPPLIER_PHAETON,
        )
        self._run('--articles=CF-100')
        phaeton.refresh_from_db()
        self.assertEqual(phaeton.title, 'Phaeton item')
        self.assertEqual(phaeton.price, 1111)
        self.assertEqual(phaeton.supplier, Product.SUPPLIER_PHAETON)

    def test_new_product_status_always_hidden(self):
        self._run()
        statuses = set(
            Product.objects.filter(seller_profile=self.seller).values_list('status', flat=True)
        )
        self.assertEqual(statuses, {'hidden'})

    def test_cost_price_not_public(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        product.status = 'active'
        product.save(update_fields=['status'])
        response = self.client.get(product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(COST_MARKER))
        self.assertNotContains(response, 'cost_price')

    def test_one_row_error_does_not_stop_batch(self):
        _write_xlsx(
            self.price_xlsx,
            ['Артикул', 'Категория', 'Название', 'Марка', 'Модель', 'Применимость', 'Цена'],
            [
                ['', 'AIR FILTER', 'Bad', 'HAVAL', 'Jolion', 'HAVAL Jolion', 100],
                ['AF-200', 'AIR FILTER', 'Good', 'HAVAL', 'Jolion', 'HAVAL Jolion', 1500],
            ],
        )
        output = self._run()
        self.assertIn('ERROR', output)
        self.assertTrue(
            Product.objects.filter(seller_profile=self.seller, article='AF-200').exists()
        )

    def test_zip_photos_are_indexed(self):
        archive = self.root / 'filters.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.write(self.photos_dir / 'AF-200.png', 'масляные фильтры/AF-200.png')
        stdout = StringIO()
        call_command(
            'import_ag_parts',
            f'--price-xlsx={self.price_xlsx}',
            f'--cost-xlsx={self.cost_xlsx}',
            f'--photos={archive}',
            f'--seller-profile-id={self.seller.pk}',
            '--articles=AF-200',
            stdout=stdout,
        )
        product = Product.objects.get(article='AF-200', seller_profile=self.seller)
        self.assertTrue(product.main_image)

    def test_update_does_not_force_active_to_hidden(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        product.status = 'active'
        product.save(update_fields=['status'])
        self._run('--articles=CF-100')
        product.refresh_from_db()
        self.assertEqual(product.status, 'active')

    def test_b2b_terms_are_not_created(self):
        from catalog.models import ProductConsignment, ProductPromotion

        self._run('--articles=CF-100', '--wholesale-min-qty=10')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.price_tiers.count(), 0)
        self.assertEqual(product.promotions.count(), 0)
        self.assertFalse(ProductPromotion.objects.filter(product=product).exists())
        self.assertFalse(ProductConsignment.objects.filter(product=product).exists())

    def test_stock_qty_not_imported(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertIsNone(product.stock_qty)

    def test_empty_retail_price_is_safe(self):
        _write_xlsx(
            self.price_xlsx,
            ['Артикул', 'Категория', 'Название', 'Марка', 'Модель', 'Применимость', 'Цена'],
            [['AF-200', 'AIR FILTER', 'No price', 'HAVAL', 'Jolion', 'HAVAL Jolion', None]],
        )
        output = self._run('--articles=AF-200')
        product = Product.objects.get(article='AF-200', seller_profile=self.seller)
        self.assertIsNone(product.price)
        self.assertTrue(product.price_on_request)
        self.assertIn('missing_retail_price', output)

    def test_zero_retail_price_is_invalid(self):
        _write_xlsx(
            self.price_xlsx,
            ['Артикул', 'Категория', 'Название', 'Марка', 'Модель', 'Применимость', 'Цена'],
            [['AF-200', 'AIR FILTER', 'Zero', 'HAVAL', 'Jolion', 'HAVAL Jolion', 0]],
        )
        output = self._run('--articles=AF-200')
        product = Product.objects.get(article='AF-200', seller_profile=self.seller)
        self.assertIsNone(product.price)
        self.assertTrue(product.price_on_request)
        self.assertIn('invalid_retail_price', output)

    def test_spark_plug_maps_to_electric(self):
        _write_xlsx(
            self.price_xlsx,
            ['Артикул', 'Категория', 'Название', 'Марка', 'Модель', 'Применимость', 'Цена'],
            [['SP-1', 'SPARK PLUG', 'Свеча', 'HAVAL', 'Jolion', 'HAVAL Jolion', 2500]],
        )
        self._run('--articles=SP-1')
        product = Product.objects.get(article='SP-1', seller_profile=self.seller)
        self.assertEqual(product.category, self.electric)
        self.assertEqual(product.price, 2500)
        self.assertFalse(product.price_on_request)

    def test_description_can_be_stored(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        product.description = 'Салонный фильтр для Chery Tiggo 7 / Tiggo 8. OEM: CF-100.'
        product.save(update_fields=['description'])
        product.refresh_from_db()
        self.assertIn('Tiggo 7', product.description)

    def test_description_imported_from_excel(self):
        text = (
            'Салонный фильтр для Chery Tiggo 7 / Tiggo 8. '
            'OEM: CF-100. Перед заказом сверьте артикул или VIN.'
        )
        _write_xlsx(
            self.price_xlsx,
            [
                'Артикул',
                'Категория',
                'Название',
                'Марка',
                'Модель',
                'Применимость',
                'Цена',
                'Описание',
            ],
            [[
                'CF-100',
                'CAIBIN FILTER',
                'Салонный фильтр Chery Tiggo 7 / Tiggo 8 — CF-100',
                'CHERY',
                'Tiggo 7 / Tiggo 8',
                'CHERY Tiggo 7 / Tiggo 8',
                1200,
                text,
            ]],
        )
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.description, text)
        self.assertEqual(product.price, 1200)
        self.assertFalse(product.price_on_request)

    def test_no_local_photo_warning(self):
        (self.photos_dir / 'CF-100.png').unlink()
        (self.photos_dir / 'CF-100-2.png').unlink()
        output = self._run('--dry-run', '--articles=CF-100')
        self.assertIn('NO_LOCAL_PHOTO', output)

    def _make_legacy(self, article='CF-100', seller_name='AG Parts', **kwargs):
        defaults = {
            'title': 'Legacy AG Parts',
            'article': article,
            'price': 1425,
            'seller_name': seller_name,
            'whatsapp_number': '77001112233',
            'status': 'active',
            'seller_profile': None,
            'city': 'Алматы',
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def test_legacy_ag_parts_is_detected(self):
        legacy = self._make_legacy()
        output = self._run('--dry-run', '--articles=CF-100')
        self.assertIn('LEGACY_AG_PARTS_MATCH', output)
        self.assertIn(f'product_id={legacy.pk}', output)
        self.assertIn('LEGACY_MATCH=1', output)
        self.assertIn('CREATED=0', output)

    def test_legacy_match_does_not_create_second_product(self):
        self._make_legacy()
        self._run('--articles=CF-100')
        self.assertEqual(Product.objects.filter(article='CF-100').count(), 1)
        leftover = Product.objects.get(article='CF-100')
        self.assertIsNone(leftover.seller_profile_id)
        self.assertEqual(leftover.status, 'active')

    def test_dry_run_legacy_does_not_change_db(self):
        legacy = self._make_legacy()
        self._run('--dry-run', '--articles=CF-100')
        legacy.refresh_from_db()
        self.assertIsNone(legacy.seller_profile_id)
        self.assertEqual(legacy.status, 'active')
        self.assertEqual(legacy.price, 1425)
        self.assertEqual(Product.objects.filter(article='CF-100').count(), 1)

    def test_write_without_adopt_skips_legacy(self):
        legacy = self._make_legacy()
        output = self._run('--articles=CF-100')
        self.assertIn('legacy_requires_adoption', output)
        self.assertIn('SKIPPED', output)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.seller_profile_id)
        self.assertEqual(Product.objects.filter(article='CF-100').count(), 1)

    def test_adopt_legacy_binds_single_product(self):
        legacy = self._make_legacy()
        output = self._run('--articles=CF-100', '--adopt-legacy-products')
        self.assertIn('ADOPTED', output)
        legacy.refresh_from_db()
        self.assertEqual(legacy.seller_profile_id, self.seller.pk)
        self.assertEqual(Product.objects.filter(article='CF-100').count(), 1)

    def test_adopt_keeps_product_id(self):
        legacy = self._make_legacy()
        old_id = legacy.pk
        self._run('--articles=CF-100', '--adopt-legacy-products')
        self.assertEqual(Product.objects.get(article='CF-100').pk, old_id)

    def test_adopt_preserves_active_status(self):
        legacy = self._make_legacy(status='active')
        self._run('--articles=CF-100', '--adopt-legacy-products')
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, 'active')
        self.assertEqual(legacy.seller_profile, self.seller)

    def test_multiple_legacy_candidates_are_ambiguous(self):
        self._make_legacy()
        self._make_legacy(title='Legacy AG Parts 2')
        output = self._run('--articles=CF-100', '--adopt-legacy-products')
        self.assertIn('LEGACY_AG_PARTS_AMBIGUOUS', output)
        self.assertEqual(Product.objects.filter(article='CF-100').count(), 2)
        self.assertEqual(
            Product.objects.filter(article='CF-100', seller_profile=self.seller).count(),
            0,
        )

    def test_other_legacy_seller_name_is_not_adopted(self):
        foreign = self._make_legacy(seller_name='Other Shop')
        self._run('--articles=CF-100', '--adopt-legacy-products')
        foreign.refresh_from_db()
        self.assertIsNone(foreign.seller_profile_id)
        own = Product.objects.get(seller_profile=self.seller, article='CF-100')
        self.assertNotEqual(own.pk, foreign.pk)
        self.assertEqual(own.status, 'hidden')

    def test_other_seller_profile_is_not_adopted(self):
        other_user = User.objects.create_user(username='other2', password='secret12345')
        other = SellerProfile.objects.create(
            user=other_user,
            name='Other Shop',
            phone='77770000002',
            city='Астана',
        )
        foreign = Product.objects.create(
            title='Чужой товар',
            article='CF-100',
            price=9999,
            seller_name=other.name,
            whatsapp_number=other.phone,
            status='active',
            seller_profile=other,
            city='Астана',
        )
        self._run('--articles=CF-100', '--adopt-legacy-products')
        foreign.refresh_from_db()
        self.assertEqual(foreign.seller_profile, other)
        own = Product.objects.get(seller_profile=self.seller, article='CF-100')
        self.assertNotEqual(own.pk, foreign.pk)

    def test_other_seller_same_article_does_not_block_create(self):
        other_user = User.objects.create_user(username='other3', password='secret12345')
        other = SellerProfile.objects.create(
            user=other_user,
            name='Other Shop',
            phone='77770000003',
            city='Астана',
        )
        Product.objects.create(
            title='Чужой товар',
            article='CF-100',
            price=9999,
            seller_name=other.name,
            whatsapp_number=other.phone,
            status='active',
            seller_profile=other,
            city='Астана',
        )
        output = self._run('--articles=CF-100')
        self.assertIn('same_article_exists_for_other_seller', output)
        self.assertTrue(
            Product.objects.filter(seller_profile=self.seller, article='CF-100').exists()
        )
        self.assertEqual(Product.objects.filter(article='CF-100').count(), 2)

    def test_seller_profile_id_is_not_hardcoded(self):
        from catalog.management.commands.import_ag_parts import Command

        parser = Command().create_parser('manage.py', 'import_ag_parts')
        defaults = {
            option: action.default
            for action in parser._actions
            for option in action.option_strings
        }
        self.assertIsNone(defaults.get('--seller-profile-id'))
        other_user = User.objects.create_user(username='second-ag', password='secret12345')
        other = SellerProfile.objects.create(
            user=other_user,
            name='Second Shop',
            phone='77770000004',
            city='Астана',
        )
        stdout = StringIO()
        call_command(
            'import_ag_parts',
            f'--price-xlsx={self.price_xlsx}',
            f'--photos={self.photos_dir}',
            f'--seller-profile-id={other.pk}',
            '--articles=AF-200',
            stdout=stdout,
        )
        product = Product.objects.get(article='AF-200')
        self.assertEqual(product.seller_profile_id, other.pk)
        self.assertNotEqual(other.pk, 1)

    def test_old_excel_structure_still_works(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(seller_profile=self.seller, article='CF-100')
        self.assertEqual(product.brand, self.chery)
        self.assertEqual(
            set(product.selected_models.values_list('id', flat=True)),
            {self.tiggo7.id, self.tiggo8.id},
        )

    def test_brand_model_pair_parsing(self):
        from catalog.ag_parts_import import parse_brand_model_pairs

        pairs, warnings = parse_brand_model_pairs(
            'Exeed:TXL; Jetour:Dashing'
        )
        self.assertEqual(pairs, [('Exeed', 'TXL'), ('Jetour', 'Dashing')])
        self.assertEqual(warnings, [])

    def test_primary_brand_and_model(self):
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.brand, self.chery)
        self.assertEqual(product.car_model, self.tiggo7)

    def test_additional_selected_brands_and_models(self):
        tiggo4 = CarModel.objects.create(brand=self.chery, name='Tiggo 4')
        tiggo7_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro')
        _write_xlsx(
            self.price_xlsx,
            [
                'Артикул',
                'Категория',
                'Название',
                'Марка',
                'Модель',
                'Применимость',
                'Цена',
                'Дополнительные модели',
            ],
            [[
                'CF-100',
                'CAIBIN FILTER',
                'Салонный фильтр Chery',
                'CHERY',
                'Tiggo 7',
                'CHERY Tiggo 4 / Tiggo 7 / Tiggo 7 Pro / Tiggo 8',
                1200,
                'Chery:Tiggo 4; Chery:Tiggo 7 Pro; Chery:Tiggo 8',
            ]],
        )
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.brand, self.chery)
        self.assertEqual(product.car_model, self.tiggo7)
        self.assertEqual(
            set(product.selected_brands.values_list('id', flat=True)),
            {self.chery.id},
        )
        self.assertEqual(
            set(product.selected_models.values_list('id', flat=True)),
            {self.tiggo7.id, tiggo4.id, tiggo7_pro.id, self.tiggo8.id},
        )

    def test_additional_other_brands(self):
        exeed = Brand.objects.create(country=self.country, name='Exeed')
        jetour = Brand.objects.create(country=self.country, name='Jetour')
        txl = CarModel.objects.create(brand=exeed, name='TXL')
        dashing = CarModel.objects.create(brand=jetour, name='Dashing')
        tiggo8_pro = CarModel.objects.create(brand=self.chery, name='Tiggo 8 Pro')
        _write_xlsx(
            self.price_xlsx,
            [
                'Артикул',
                'Категория',
                'Название',
                'Марка',
                'Модель',
                'Применимость',
                'Цена',
                'Дополнительные модели',
            ],
            [[
                'CF-100',
                'OIL FILTER',
                'Масляный фильтр Chery Tiggo 8 Pro',
                'CHERY',
                'Tiggo 8 Pro',
                'Chery Tiggo 8 Pro; Exeed TXL; Jetour Dashing',
                1950,
                'Exeed:TXL; Jetour:Dashing',
            ]],
        )
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.brand, self.chery)
        self.assertEqual(product.car_model, tiggo8_pro)
        self.assertEqual(
            set(product.selected_brands.values_list('name', flat=True)),
            {'Chery', 'Exeed', 'Jetour'},
        )
        self.assertEqual(
            set(product.selected_models.values_list('id', flat=True)),
            {tiggo8_pro.id, txl.id, dashing.id},
        )

    def test_structured_unknown_brand_warning(self):
        _write_xlsx(
            self.price_xlsx,
            [
                'Артикул',
                'Категория',
                'Марка',
                'Модель',
                'Применимость',
                'Цена',
                'Дополнительные модели',
            ],
            [['ZZ-1', 'AIR FILTER', 'CHERY', 'Tiggo 7', 'CHERY Tiggo 7', 100, 'Zeekr:001']],
        )
        output = self._run('--dry-run', '--articles=ZZ-1')
        self.assertIn('unknown_brand:Zeekr', output)
        self.assertFalse(Brand.objects.filter(name='Zeekr').exists())

    def test_structured_unknown_model_is_not_created(self):
        _write_xlsx(
            self.price_xlsx,
            [
                'Артикул',
                'Категория',
                'Марка',
                'Модель',
                'Применимость',
                'Цена',
                'Дополнительные модели',
            ],
            [[
                'ZZ-2',
                'AIR FILTER',
                'CHERY',
                'Tiggo 7',
                'CHERY Tiggo 7 / Tiggo 8 Pro',
                100,
                'Chery:Tiggo 8 Pro',
            ]],
        )
        output = self._run('--articles=ZZ-2')
        self.assertIn('unknown_model:Chery:Tiggo 8 Pro', output)
        self.assertFalse(CarModel.objects.filter(name='Tiggo 8 Pro').exists())
        product = Product.objects.get(article='ZZ-2', seller_profile=self.seller)
        self.assertEqual(product.car_model, self.tiggo7)

    def test_repeat_update_does_not_duplicate_m2m(self):
        self._run('--articles=CF-100')
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertEqual(product.selected_brands.count(), 1)
        self.assertEqual(product.selected_models.count(), 2)

    def test_compatibility_saved_without_structured_extraction(self):
        _write_xlsx(
            self.price_xlsx,
            [
                'Артикул',
                'Категория',
                'Название',
                'Марка',
                'Модель',
                'Применимость',
                'Цена',
            ],
            [[
                'CF-100',
                'CAIBIN FILTER',
                'Салонный фильтр',
                'CHERY',
                'Tiggo 7',
                'Chery Tiggo 7; Wingle 6 only as text',
                1200,
            ]],
        )
        self._run('--articles=CF-100')
        product = Product.objects.get(article='CF-100', seller_profile=self.seller)
        self.assertIn('Wingle 6', product.compatibility)
        self.assertEqual(
            set(product.selected_models.values_list('id', flat=True)),
            {self.tiggo7.id},
        )

    def test_dry_run_adopt_flag_does_not_write(self):
        legacy = self._make_legacy()
        output = self._run(
            '--dry-run',
            '--articles=CF-100',
            '--adopt-legacy-products',
        )
        self.assertIn('WOULD_ADOPT', output)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.seller_profile_id)
        self.assertEqual(Product.objects.filter(article='CF-100').count(), 1)
