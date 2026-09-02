from io import StringIO
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from catalog.forms import ProductForm
from catalog.models import Brand, CarModel, Category, Country, Product, SellerProfile
from catalog.product_quality import (
    INTERNAL_RESEARCH_ERROR,
    STATUS_AUTO_FIXABLE,
    STATUS_CRITICAL,
    STATUS_MANUAL,
    STATUS_OK,
    apply_safe_fixes,
    audit_all_products,
    audit_product,
    sanitize_oem_text,
    sanitize_public_product_text,
)


def _seller(username='quality-seller', name='Quality Shop', phone='77770000801'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
        city='Алматы',
    )


def _product(**kwargs):
    defaults = {
        'title': 'Фильтр масляный',
        'price': 1500,
        'seller_name': 'Quality Shop',
        'whatsapp_number': '77770000801',
        'status': 'active',
        'article': 'QF-100',
        'city': 'Алматы',
        'compatibility': 'Toyota Camry 40',
        'description': 'Качественная запчасть. Перед установкой сверьте применимость.',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class ProductQualitySanitizerTests(TestCase):
    def test_oem_sanitizer_returns_only_numbers(self):
        cleaned = sanitize_oem_text('OEM: D20T0120700; кросс: D20T012-0700, аналог: XX')
        self.assertIn('D20T0120700', cleaned)
        self.assertIn('D20T012-0700', cleaned)
        self.assertNotIn('OEM:', cleaned)
        self.assertNotIn('кросс', cleaned.casefold())
        self.assertNotIn('аналог', cleaned.casefold())

    def test_oem_sanitizer_rejects_short_generic_tokens(self):
        cleaned = sanitize_oem_text('AG 302 ECO SA SB 8147 A1003 A-1180')
        self.assertEqual(cleaned, '')
        kept = sanitize_oem_text('F081109111HD F08-1109111HD 28113-2S000 AG 302')
        lines = kept.split('\n')
        self.assertIn('F081109111HD', lines)
        self.assertIn('F08-1109111HD', lines)
        self.assertIn('28113-2S000', lines)
        self.assertNotIn('AG', lines)
        self.assertNotIn('302', lines)

    def test_compatibility_preview_drops_research_sentence(self):
        raw = (
            'Changan CS75 Plus, UNI-K — 2.0T. '
            'CS85/CS95 есть у поставщиков, в справочнике ZPT нет.'
        )
        cleaned = sanitize_public_product_text(raw, field='compatibility', mode='preview')
        self.assertIn('CS75 Plus', cleaned)
        self.assertNotIn('поставщиков', cleaned)
        self.assertNotIn('ZPT', cleaned)


class ProductQualityAuditTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='Корея Q')
        self.brand = Brand.objects.create(country=self.country, name='Hyundai')
        self.model = CarModel.objects.create(brand=self.brand, name='Sonata')
        self.category = Category.objects.create(name='Ходовая часть Q')
        self.seller = _seller()

    def test_normal_product_is_ok(self):
        product = _product(
            title='Сайлентблок рычага Hyundai Sonata',
            article='OK-100',
            slug='ok-100',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
        )
        result = audit_product(product)
        self.assertEqual(result.status, STATUS_OK)

    def test_internal_research_text_is_manual_or_auto(self):
        product = _product(
            title='Свеча зажигания',
            article='INT-1',
            slug='int-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            compatibility=(
                'Changan CS75 Plus, UNI-K — 2.0T. '
                'Lamore встречается у поставщиков, в справочнике ZPT нет.'
            ),
        )
        result = audit_product(product)
        self.assertIn(result.status, {STATUS_AUTO_FIXABLE, STATUS_MANUAL})
        self.assertTrue(any(issue.startswith('INTERNAL_TEXT') for issue in result.issues))

    def test_raw_gemini_chat_is_critical(self):
        product = _product(
            title='Сайлентблок рычага',
            article='ZTT-CH-002A',
            slug='ztt-ch-002a',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            description=(
                'Чат с Gemini\n'
                'на какую машину подходит этот пыльник\n'
                'Судя по этикетке на упаковке детали в файле WhatsApp Image 1.jpeg '
                + ('пыльник ШРУСа ' * 400)
            ),
        )
        result = audit_product(product)
        self.assertEqual(result.status, STATUS_CRITICAL)
        self.assertTrue(any('CRITICAL' in issue for issue in result.issues))

    def test_conflicting_article_type_is_critical(self):
        # Production may store duplicates with empty seller_profile; unique
        # constraint is (seller_profile, article) and allows multiple NULLs.
        _product(
            title='Амортизатор передний правый',
            article='54660-4H100',
            slug='shock-54660',
            seller_profile=None,
            seller_name='KOREA-PARTS',
            brand=self.brand,
            category=self.category,
        )
        _product(
            title='Тяга рулевая левая',
            article='54660-4H100',
            slug='rod-54660',
            seller_profile=None,
            seller_name='KOREA-PARTS',
            brand=self.brand,
            category=self.category,
        )
        _product(
            title='Наконечник рулевой тяги правый',
            article='54660-4H100',
            slug='end-54660',
            seller_profile=None,
            seller_name='KOREA-PARTS',
            brand=self.brand,
            category=self.category,
        )
        results = audit_all_products(Product.objects.filter(article='54660-4H100'))
        self.assertTrue(all(item.status == STATUS_CRITICAL for item in results))
        self.assertTrue(
            any('CRITICAL_ARTICLE_CONFLICT' in item.issues for item in results)
        )

    def test_generic_seller_description_is_manual(self):
        product = _product(
            title='Колодки тормозные',
            article='KR-1',
            slug='kr-1',
            seller_profile=self.seller,
            seller_name='KOREA-PARTS',
            brand=self.brand,
            category=self.category,
            description=(
                'ЦЕНЫ ВСЕГДА ЗА 1 ШТ ТОВАРА. Оригинальные запчасти на TOYOTA и LEXUS. '
                'Наличие уточняйте у менеджера.'
            ),
        )
        result = audit_product(product)
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertIn('GENERIC_SELLER_DESCRIPTION', result.issues)

    def test_malformed_article_with_oem_label(self):
        safe = _product(
            title='Сайлентблок',
            article='FPE240 (кросс-номер OEM: 5080868AA)',
            slug='fpe240',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
        )
        manual = _product(
            title='Сайлентблок',
            article='OEM-номер / Кросс-номер: 05066024AA, 413ST09010',
            slug='oem-only',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
        )
        safe_result = audit_product(safe)
        manual_result = audit_product(manual)
        self.assertEqual(safe_result.status, STATUS_AUTO_FIXABLE)
        self.assertIn('MALFORMED_ARTICLE', safe_result.issues)
        self.assertEqual(safe_result.safe_fixes.get('article'), 'FPE240')
        self.assertIn('5080868AA', safe_result.safe_fixes.get('oem_cross_references', ''))
        self.assertEqual(manual_result.status, STATUS_MANUAL)
        self.assertIn('MALFORMED_ARTICLE', manual_result.issues)

    def test_cv_boot_morphology_is_not_title_description_critical(self):
        product = _product(
            title='Пыльник ШРУСа Japanparts KB-306',
            article='KB-306',
            slug='kb-306-boot',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            description='Ремонтный комплект пыльника внутреннего ШРУС для замены резины.',
        )
        result = audit_product(product)
        self.assertFalse(any('title и description' in issue for issue in result.issues))
        self.assertNotEqual(result.status, STATUS_CRITICAL)

    def test_cv_boot_uppercase_shrus_is_not_false_critical(self):
        product = _product(
            title='Пыльник ШРУСА',
            article='304887-ok',
            slug='304887-ok',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            description='Данный пыльник устанавливается на ШРУС и защищает шарнир.',
        )
        result = audit_product(product)
        self.assertFalse(any('title и description' in issue for issue in result.issues))
        self.assertNotEqual(result.status, STATUS_CRITICAL)

    def test_silentblock_with_gemini_cv_boot_chat_is_critical(self):
        product = _product(
            title='Сайлентблок рычага',
            article='ZTT-CH-002A-reg',
            slug='ztt-ch-002a-reg',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            description=(
                'Чат с Gemini\n'
                'на какую машину подходит этот пыльник\n'
                'Судя по этикетке на упаковке детали в файле WhatsApp Image 1.jpeg '
                + ('пыльник ШРУСа ' * 400)
            ),
        )
        result = audit_product(product)
        self.assertEqual(result.status, STATUS_CRITICAL)

    def test_spark_in_electrical_without_specialized_category_is_not_broad(self):
        electrical = Category.objects.create(name='Электрика')
        product = _product(
            title='Свеча зажигания Changan CS75 Plus',
            article='D20T0120700',
            slug='d20t-spark',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=electrical,
        )
        result = audit_product(product)
        self.assertNotIn('BROAD_CATEGORY', result.issues)
        self.assertIn('CATEGORY_SCHEMA_GAP', result.issues)
        self.assertNotEqual(result.status, STATUS_MANUAL)

    def test_spark_in_electrical_with_specialized_category_is_broad(self):
        electrical = Category.objects.create(name='Электрика')
        Category.objects.create(name='Свечи зажигания')
        product = _product(
            title='Свеча зажигания Changan CS75 Plus',
            article='F4J163707010',
            slug='f4j-spark',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=electrical,
        )
        result = audit_product(product)
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertIn('BROAD_CATEGORY', result.issues)

    def test_selected_brands_without_primary_brand_is_not_missing(self):
        product = _product(
            title='Фильтр салонный',
            article='SEL-BRAND-1',
            slug='sel-brand-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=self.category,
        )
        product.selected_brands.add(self.brand)
        result = audit_product(product)
        self.assertNotIn('MISSING_BRAND', result.issues)

    def test_no_brand_and_no_selected_brands_is_missing(self):
        product = _product(
            title='Фильтр салонный',
            article='NO-BRAND-1',
            slug='no-brand-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=self.category,
        )
        result = audit_product(product)
        self.assertIn('MISSING_BRAND', result.issues)
        self.assertEqual(result.status, STATUS_MANUAL)

    def test_selected_models_without_primary_model_is_not_missing_fitment(self):
        product = _product(
            title='Фильтр салонный',
            article='SEL-MODEL-1',
            slug='sel-model-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=None,
            category=self.category,
            compatibility='',
        )
        product.selected_models.add(self.model)
        result = audit_product(product)
        self.assertNotIn('MISSING_COMPATIBILITY', result.issues)

    def test_article_role_unclear_is_manual_not_type_critical(self):
        product = _product(
            title='Пыльник ШРУСА',
            article='304886',
            slug='ptp-304886',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            compatibility=(
                'Chery Tiggo 7 Pro.\n'
                'Артикул производителя: PTP023723\n'
                'Кросс-номер: 304886'
            ),
            description='Данный пыльник устанавливается на внутренний ШРУС.',
        )
        result = audit_product(product)
        self.assertFalse(any('title и description' in issue for issue in result.issues))
        self.assertNotEqual(result.status, STATUS_CRITICAL)
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertIn('ARTICLE_ROLE_UNCLEAR', result.issues)
        self.assertFalse(result.safe_fixes.get('article'))

    def test_metadata_compatibility_block_is_manual_not_auto(self):
        raw_compat = (
            'Основные данные и артикулы\n'
            'Наименование: Пыльник ШРУСа\n'
            'Артикул (Номер производителя): PTP023723\n'
            'Кросс-номер (артикул аналога): 304887\n'
        )
        product = _product(
            title='Пыльник ШРУСА',
            article='304887',
            slug='ptp-304887-meta',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            compatibility=raw_compat,
            description='Данный пыльник устанавливается на внутренний ШРУС.',
        )
        result = audit_product(product)
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertIn('COMPATIBILITY_NOT_FITMENT', result.issues)
        self.assertIn('ARTICLE_ROLE_UNCLEAR', result.issues)
        self.assertNotIn('compatibility', result.safe_fixes)
        self.assertEqual(product.compatibility, raw_compat)
        self.assertFalse(result.safe_fixes.get('article'))

    def test_normal_compatibility_is_not_metadata(self):
        product = _product(
            title='Свеча зажигания Changan CS75 Plus',
            article='D20-FIT-1',
            slug='d20-fit-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            compatibility='Changan CS75 Plus, UNI-K — 2.0T',
        )
        result = audit_product(product)
        self.assertNotIn('COMPATIBILITY_NOT_FITMENT', result.issues)
        self.assertNotIn('ARTICLE_ROLE_UNCLEAR', result.issues)

    def test_duplicate_description_sentence_is_idempotent(self):
        product = _product(
            title='Свеча зажигания Changan CS75 Plus',
            article='D20T0120700-dup',
            slug='d20t-dup',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            compatibility='Changan CS75 Plus, UNI-K — 2.0T',
            description=(
                'Свеча зажигания предназначена для воспламенения смеси.\n'
                'В карточке одна свеча, не комплект.\n'
                'Перед заказом рекомендуется проверить VIN.\n'
                'В карточке одна свеча, не комплект.\n'
            ),
        )
        first = audit_product(product)
        self.assertIn('description', first.safe_fixes)
        self.assertEqual(
            first.safe_fixes['description'].casefold().count('в карточке одна свеча, не комплект.'),
            1,
        )
        apply_safe_fixes(product, first.safe_fixes)
        product.refresh_from_db()
        second = audit_product(product)
        self.assertEqual(second.safe_fixes, {})
        self.assertEqual(
            product.description.casefold().count('в карточке одна свеча, не комплект.'),
            1,
        )

    def test_normal_paragraph_is_not_rewrapped_as_safe_fix(self):
        description = (
            'Свеча зажигания для бензиновых двигателей Chery. '
            'Артикул F4J163707010. Основная применимость: Tiggo 7 Pro. '
            'В карточке одна свеча, не комплект.'
        )
        product = _product(
            title='Свеча зажигания Chery',
            article='F4J163707010-layout',
            slug='f4j-layout',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            compatibility='Chery Tiggo 7 Pro',
            description=description,
        )
        result = audit_product(product)
        self.assertNotIn('description', result.safe_fixes)
        self.assertEqual(product.description, description)
        self.assertNotEqual(result.status, STATUS_AUTO_FIXABLE)

    def test_same_line_sentences_keep_layout_without_duplicate(self):
        description = (
            'Свеча зажигания для бензиновых двигателей Chery. Артикул F4J163707010. '
            'Основная применимость указана в карточке.'
        )
        product = _product(
            title='Свеча зажигания Chery',
            article='F4J-SAME-LINE',
            slug='f4j-same-line',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            compatibility='Chery Tiggo 7 Pro',
            description=description,
        )
        result = audit_product(product)
        self.assertNotIn('description', result.safe_fixes)
        self.assertNotIn('\n', description)

    def test_exact_glued_duplicate_block_is_deduped_idempotently(self):
        block = (
            'Пыльник ШРУСа Japanparts KB-377 предназначен для защиты шарнира равных угловых скоростей. '
            'Высококачественный комплект обеспечивает герметичность соединения. '
            'Пыльник надежно защищает от грязи и влаги, обеспечивая плавный ход автомобиля.'
        )
        product = _product(
            title='Пыльник ШРУСа Japanparts KB-377',
            article='KB-377',
            slug='kb-377-block',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            description=block + block,
        )
        first = audit_product(product)
        self.assertIn('description', first.safe_fixes)
        cleaned = first.safe_fixes['description']
        self.assertEqual(cleaned.count('Пыльник ШРУСа Japanparts KB-377'), 1)
        self.assertIn('плавный ход автомобиля.', cleaned)
        apply_safe_fixes(product, first.safe_fixes)
        product.refresh_from_db()
        before_brand = product.brand_id
        before_price = product.price
        second = audit_product(product)
        self.assertEqual(second.safe_fixes, {})
        self.assertEqual(product.brand_id, before_brand)
        self.assertEqual(product.price, before_price)

    def test_external_seller_link_is_manual(self):
        product = _product(
            title='Салонный фильтр Toyota Prado',
            article='FC41254',
            slug='fc41254-link',
            seller_profile=self.seller,
            seller_name='AG Parts',
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            description=(
                'Салонный фильтр для Toyota Land Cruiser Prado.\n'
                'Подробнее: https://grm4x4.kz/catalog/fc41254'
            ),
        )
        result = audit_product(product)
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertIn('EXTERNAL_SELLER_LINK', result.issues)

    def test_manufacturer_url_is_not_external_seller_link(self):
        product = _product(
            title='Пыльник ШРУСа Japanparts KB-377',
            article='KB-377-mfr',
            slug='kb-377-mfr',
            seller_profile=self.seller,
            seller_name='AG Parts',
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            description=(
                'Пыльник ШРУСа Japanparts KB-377.\n'
                'Каталог производителя: https://www.japanparts.com/catalog/kb-377'
            ),
        )
        result = audit_product(product)
        self.assertNotIn('EXTERNAL_SELLER_LINK', result.issues)

    def test_malformed_truncated_fitment_description_is_manual(self):
        product = _product(
            title='Свеча зажигания Chery',
            article='F4J16-3707010',
            slug='f4j16-malformed-desc',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            description=(
                'Chery Arrizo 5 Plus. (11.2020 - н. ...\n'
                'Chery Arrizo 6 Pro. (11.2020 - н. ...\n'
                'Chery Arrizo 8. (05.2022 - н. ...\n'
                'Chery Tiggo 4 Pro. (08.2020 - н. ...\n'
                'Chery Tiggo 7 Pro. (11.2019 - н. ...\n'
                'Chery Tiggo 8 Pro. (12.2020 - н. ...\n'
            ),
        )
        result = audit_product(product)
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertIn('MALFORMED_DESCRIPTION', result.issues)
        self.assertNotIn('description', result.safe_fixes)

    def test_malformed_cv_boot_articles_are_manual_not_critical(self):
        japanparts = _product(
            title='Пыльник ШРУСа Japanparts KB-306',
            article='Артикул: KB-306 Оригинальный кросс-номер OEM: 480693TA0A',
            slug='kb-306-malformed',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            description='Ремонтный комплект пыльника ШРУСа.',
        )
        stellox = _product(
            title='Пыльник ШРУСа Stellox 13-03014-SX',
            article='Артикул: 13-03014-SX Оригинальный кросс-номер OEM: 480693TA0A',
            slug='stellox-malformed',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=self.category,
            description='Защитный комплект пыльника ШРУСа.',
        )
        japan_result = audit_product(japanparts)
        stellox_result = audit_product(stellox)
        self.assertEqual(japan_result.status, STATUS_MANUAL)
        self.assertIn('MALFORMED_ARTICLE', japan_result.issues)
        self.assertFalse(any('title и description' in issue for issue in japan_result.issues))
        self.assertNotIn('article', japan_result.safe_fixes)
        self.assertEqual(stellox_result.status, STATUS_MANUAL)
        self.assertIn('MALFORMED_ARTICLE', stellox_result.issues)
        self.assertIn('MISSING_BRAND', stellox_result.issues)
        self.assertFalse(any('title и description' in issue for issue in stellox_result.issues))

    def test_apply_safe_fixes_second_audit_has_no_same_fix(self):
        product = _product(
            title='Фильтр салонный',
            article='S1010140400',
            slug='idem-s101',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            description=(
                'Салонный фильтр Chery.\n'
                'OEM: S1010140400; S101014-0400.\n'
                'OEM: 1109190CR01.'
            ),
            oem_cross_references='S1010140400\nS101014-0400\n1109190CR01',
        )
        first = audit_product(product)
        self.assertTrue(first.safe_fixes)
        apply_safe_fixes(product, first.safe_fixes)
        product.refresh_from_db()
        second = audit_product(product)
        self.assertEqual(second.safe_fixes, {})
        self.assertNotIn('WHITESPACE_OR_SAFE_NORMALIZE', second.issues)
        self.assertNotIn('OEM:', product.description)


class ProductQualityFormGuardTests(TestCase):
    def test_new_dirty_payload_is_rejected(self):
        form = ProductForm(data={
            'title': 'Свеча',
            'article': 'NEW-DIRTY',
            'price': '1000',
            'condition': 'new',
            'status': 'active',
            'compatibility': 'CS85 есть у поставщиков, в справочнике ZPT нет.',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('compatibility', form.errors)
        self.assertIn(INTERNAL_RESEARCH_ERROR, form.errors['compatibility'][0])

    def test_legacy_dirty_field_can_be_saved_if_unchanged(self):
        seller = _seller('legacy-form', 'Legacy Shop', '77770000802')
        product = _product(
            title='Свеча',
            article='LEGACY-1',
            slug='legacy-1',
            seller_profile=seller,
            seller_name=seller.name,
            compatibility='CS85 есть у поставщиков, в справочнике ZPT нет.',
            price=1000,
        )
        form = ProductForm(
            data={
                'title': 'Свеча обновлённая',
                'article': 'LEGACY-1',
                'price': '1000',
                'condition': 'new',
                'status': 'active',
                'compatibility': product.compatibility,
                'description': product.description,
            },
            instance=product,
        )
        self.assertTrue(form.is_valid(), form.errors)


class ProductQualityCommandTests(TestCase):
    def setUp(self):
        self.seller = _seller('cmd-seller', 'CMD Shop', '77770000803')
        self.product = _product(
            title='  Сайлентблок  ',
            article='FPE240 (кросс-номер OEM: 5080868AA)',
            slug='cmd-fpe240',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            price=2200,
            status='active',
            compatibility='Changan CS75 Plus',
            description='Сайлентблок рычага.\nOEM: 5080868AA',
            oem_cross_references='',
        )

    def test_default_dry_run_does_not_write_db(self):
        before_article = self.product.article
        before_price = self.product.price
        with tempfile.TemporaryDirectory() as tmp:
            call_command(
                'clean_product_cards',
                '--dry-run',
                '--report',
                tmp,
            )
        self.product.refresh_from_db()
        self.assertEqual(self.product.article, before_article)
        self.assertEqual(self.product.price, before_price)

    def test_audit_command_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            call_command('audit_product_cards', '--report', tmp)
            files = list(Path(tmp).glob('product_card_audit*.csv'))
            self.assertTrue(files)
            content = files[0].read_text(encoding='utf-8')
            self.assertIn('product_id', content)
            self.assertIn(str(self.product.pk), content)

    def test_apply_safe_changes_only_whitelisted_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            call_command(
                'clean_product_cards',
                '--apply-safe',
                '--product-id',
                str(self.product.pk),
                '--report',
                tmp,
            )
        self.product.refresh_from_db()
        self.assertEqual(self.product.article, 'FPE240')
        self.assertIn('5080868AA', self.product.oem_cross_references)
        self.assertEqual(self.product.price, 2200)
        self.assertEqual(self.product.status, 'active')
        self.assertEqual(self.product.seller_profile_id, self.seller.pk)
        self.assertEqual(self.product.seller_name, 'CMD Shop')
        self.assertNotIn('OEM:', self.product.description)

    def test_second_apply_safe_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            call_command(
                'clean_product_cards',
                '--apply-safe',
                '--product-id',
                str(self.product.pk),
                '--report',
                tmp,
                stdout=StringIO(),
            )
            out = StringIO()
            call_command(
                'clean_product_cards',
                '--apply-safe',
                '--product-id',
                str(self.product.pk),
                '--report',
                tmp,
                stdout=out,
            )
        self.assertRegex(out.getvalue(), r'changed 0\b')

    def test_duplicate_description_second_apply_is_noop(self):
        product = _product(
            title='Свеча зажигания',
            article='DUP-CMD-1',
            slug='dup-cmd-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            compatibility='Changan CS75 Plus, UNI-K — 2.0T',
            description=(
                'Свеча зажигания для двигателя.\n'
                'В карточке одна свеча, не комплект.\n'
                'В карточке одна свеча, не комплект.\n'
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            call_command(
                'clean_product_cards',
                '--apply-safe',
                '--product-id',
                str(product.pk),
                '--report',
                tmp,
                stdout=StringIO(),
            )
            product.refresh_from_db()
            self.assertEqual(
                product.description.casefold().count('в карточке одна свеча, не комплект.'),
                1,
            )
            out = StringIO()
            call_command(
                'clean_product_cards',
                '--apply-safe',
                '--product-id',
                str(product.pk),
                '--report',
                tmp,
                stdout=out,
            )
        self.assertRegex(out.getvalue(), r'changed 0\b')
