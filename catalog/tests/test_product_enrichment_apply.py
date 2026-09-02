import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from catalog.models import Brand, CarModel, Category, Country, Product, SellerProfile
from catalog.product_enrichment_apply import (
    STATUS_ALREADY_APPLIED,
    STATUS_CHANGED,
    STATUS_ERROR,
    STATUS_READY,
    STATUS_STALE,
    apply_preview_snapshot,
)
from catalog.product_quality import detect_internal_research_text


def _seller(username='apply-seller', name='AG Parts Apply', phone='77770000911'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
        city='Алматы',
    )


def _product(**kwargs):
    defaults = {
        'title': 'Фильтр салонный',
        'price': 4200,
        'seller_name': 'AG Parts Apply',
        'whatsapp_number': '77770000911',
        'status': 'active',
        'article': 'APPLY-100',
        'city': 'Алматы',
        'stock_qty': 7,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _snapshot_row(product, **overrides):
    brand_name = product.brand.name if product.brand_id and product.brand else ''
    category_name = product.category.name if product.category_id and product.category else ''
    row = {
        'ok': True,
        'product_id': product.pk,
        'current_article': product.article or '',
        'current_title': product.title or '',
        'current_brand': brand_name,
        'current_brand_id': product.brand_id,
        'current_category': category_name,
        'current_category_id': product.category_id,
        'current_compatibility': product.compatibility or '',
        'current_engine_compatibility': product.engine_compatibility or '',
        'current_oem_cross_references': product.oem_cross_references or '',
        'current_description': product.description or '',
        'suggested_title': product.title or '',
        'suggested_brand': brand_name,
        'suggested_brand_id': product.brand_id,
        'suggested_category': category_name,
        'suggested_category_id': product.category_id,
        'suggested_compatibility': product.compatibility or '',
        'suggested_engine_compatibility': product.engine_compatibility or '',
        'suggested_oem_cross_references': product.oem_cross_references or '',
        'suggested_description': product.description or '',
        'approved_fields': [],
        'blocked_fields': [],
        'field_decisions': {
            'title': 'unchanged',
            'brand': 'unchanged',
            'category': 'unchanged',
            'compatibility': 'unchanged',
            'engine_compatibility': 'unchanged',
            'oem_cross_references': 'unchanged',
            'description': 'unchanged',
        },
        'dictionary_additions': {'brands': [], 'categories': []},
        'unresolved_fields': [],
        'research_notes': [{'text': 'Только для ревью, не писать в Product.', 'severity': 'info'}],
        'evidence_notes': [{'text': 'evidence only', 'severity': 'info'}],
        'sources': [{'title': 'FitInPart', 'url': 'https://example.com/fit'}],
        'fields': {
            'title': product.title or '',
            'brand_name': brand_name,
            'brand_id': product.brand_id,
            'category_name': category_name,
            'category_id': product.category_id,
            'compatibility': product.compatibility or '',
            'engine_compatibility': product.engine_compatibility or '',
            'oem_cross_references': product.oem_cross_references or '',
            'description': product.description or '',
        },
    }
    row.update(overrides)
    decisions = dict(row['field_decisions'])
    for name in row.get('approved_fields') or []:
        decisions[name] = 'approved'
    for name in row.get('blocked_fields') or []:
        decisions[name] = 'blocked'
    row['field_decisions'] = decisions
    return row


def _write_snapshot(directory, products_rows, stem='reviewed_v1') -> Path:
    path = Path(directory) / f'{stem}.json'
    payload = {
        'generated_at': '2026-09-02T08:00:00+00:00',
        'git_commit': 'testcommit',
        'stem': stem,
        'summary': {'total': len(products_rows), 'ok': len(products_rows), 'missing': 0},
        'products': products_rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def _protected(product: Product) -> dict:
    return {
        'price': product.price,
        'status': product.status,
        'stock_qty': product.stock_qty,
        'seller_profile_id': product.seller_profile_id,
        'seller_name': product.seller_name,
        'article': product.article,
        'car_model_id': product.car_model_id,
        'main_image': str(product.main_image or ''),
    }


class ProductEnrichmentApplyTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='Китай Apply')
        self.brand = Brand.objects.create(country=self.country, name='Chery')
        self.jac = Brand.objects.create(country=self.country, name='JAC')
        self.model = CarModel.objects.create(brand=self.brand, name='Tiggo 7')
        self.category = Category.objects.create(name='Фильтры')
        self.cabin = Category.objects.create(name='Салонные фильтры')
        self.seller = _seller()

    def _run(self, preview_file, product_ids, *, apply=False, report_dir=None):
        def boom(*args, **kwargs):
            raise AssertionError('OpenAI must not be called during apply')

        with patch('catalog.product_assistant.call_openai_product_lookup', side_effect=boom):
            return apply_preview_snapshot(
                preview_file=Path(preview_file),
                product_ids=list(product_ids),
                apply=apply,
                report_dir=Path(report_dir),
            )

    def test_dry_run_changes_nothing(self):
        product = _product(
            title='Фильтр воздушный',
            article='APPLY-A',
            slug='apply-a',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=None,
            compatibility='',
            description='Старое описание',
        )
        before = _protected(product)
        row = _snapshot_row(
            product,
            approved_fields=['title', 'brand', 'category', 'compatibility'],
            suggested_title='Фильтр воздушный JAC S5',
            suggested_brand='JAC',
            suggested_brand_id=self.jac.pk,
            suggested_category='Фильтры',
            suggested_category_id=self.category.pk,
            suggested_compatibility='JAC S5',
            fields={
                'title': 'Фильтр воздушный JAC S5',
                'brand_name': 'JAC',
                'brand_id': self.jac.pk,
                'category_name': 'Фильтры',
                'category_id': self.category.pk,
                'compatibility': 'JAC S5',
            },
        )
        counts = (
            Product.objects.count(),
            Brand.objects.count(),
            Category.objects.count(),
            CarModel.objects.count(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, [row])
            outcome = self._run(preview, [product.pk], apply=False, report_dir=tmp)
            self.assertEqual(outcome['results'][0]['status'], STATUS_READY)
            self.assertEqual(outcome['summary']['changed'], 0)
            self.assertEqual(outcome['summary']['ready'], 1)
            call_command(
                'apply_product_enrichment',
                '--preview-file',
                str(preview),
                '--product-ids',
                str(product.pk),
                '--report',
                tmp,
            )
        product.refresh_from_db()
        self.assertEqual(product.title, 'Фильтр воздушный')
        self.assertIsNone(product.brand_id)
        self.assertIsNone(product.category_id)
        self.assertEqual(product.compatibility, '')
        self.assertEqual(product.description, 'Старое описание')
        self.assertEqual(_protected(product), before)
        self.assertEqual(
            (
                Product.objects.count(),
                Brand.objects.count(),
                Category.objects.count(),
                CarModel.objects.count(),
            ),
            counts,
        )

    def test_apply_writes_only_approved_fields(self):
        product = _product(
            title='Фильтр воздушный',
            article='APPLY-B',
            slug='apply-b',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=None,
            compatibility='',
            engine_compatibility='',
            oem_cross_references='',
            description='Старое описание',
        )
        before = _protected(product)
        row = _snapshot_row(
            product,
            approved_fields=['title', 'brand', 'category', 'compatibility'],
            blocked_fields=['engine_compatibility'],
            suggested_title='Фильтр воздушный JAC S5',
            suggested_brand='JAC',
            suggested_brand_id=self.jac.pk,
            suggested_category='Фильтры',
            suggested_category_id=self.category.pk,
            suggested_compatibility='JAC S5',
            suggested_engine_compatibility='1.5 Turbo',
            suggested_oem_cross_references='OEM-HACK',
            suggested_description='Не писать это описание',
            fields={
                'title': 'Фильтр воздушный JAC S5',
                'brand_name': 'JAC',
                'brand_id': self.jac.pk,
                'category_name': 'Фильтры',
                'category_id': self.category.pk,
                'compatibility': 'JAC S5',
                'engine_compatibility': '1.5 Turbo',
                'oem_cross_references': 'OEM-HACK',
                'description': 'Не писать это описание',
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, [row])
            outcome = self._run(preview, [product.pk], apply=True, report_dir=tmp)
        product.refresh_from_db()
        self.assertEqual(outcome['results'][0]['status'], STATUS_CHANGED)
        self.assertEqual(
            outcome['results'][0]['changed_fields'],
            ['title', 'brand', 'category', 'compatibility'],
        )
        self.assertEqual(product.title, 'Фильтр воздушный JAC S5')
        self.assertEqual(product.brand_id, self.jac.pk)
        self.assertEqual(product.category_id, self.category.pk)
        self.assertEqual(product.compatibility, 'JAC S5')
        self.assertEqual(product.engine_compatibility, '')
        self.assertEqual(product.oem_cross_references, '')
        self.assertEqual(product.description, 'Старое описание')
        self.assertEqual(_protected(product), before)

    def test_blocked_and_unchanged_fields_never_written(self):
        product = _product(
            title='Старый title',
            article='APPLY-C',
            slug='apply-c',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            compatibility='',
            description='Оставить',
        )
        row = _snapshot_row(
            product,
            approved_fields=['compatibility'],
            blocked_fields=['brand'],
            suggested_title='ХАКНУТЫЙ TITLE',
            suggested_brand='JAC',
            suggested_brand_id=self.jac.pk,
            suggested_category='Салонные фильтры',
            suggested_category_id=self.cabin.pk,
            suggested_compatibility='Chery Tiggo 7',
            suggested_description='ХАКНУТОЕ описание ChatGPT',
        )
        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, [row])
            self._run(preview, [product.pk], apply=True, report_dir=tmp)
        product.refresh_from_db()
        self.assertEqual(product.title, 'Старый title')
        self.assertEqual(product.brand_id, self.brand.pk)
        self.assertEqual(product.category_id, self.category.pk)
        self.assertEqual(product.compatibility, 'Chery Tiggo 7')
        self.assertEqual(product.description, 'Оставить')

    def test_dictionary_counts_unchanged(self):
        product = _product(
            title='Фильтр',
            article='APPLY-D',
            slug='apply-d',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=None,
            compatibility='',
        )
        counts = (
            Brand.objects.count(),
            Category.objects.count(),
            CarModel.objects.count(),
        )
        row = _snapshot_row(
            product,
            approved_fields=['brand', 'category', 'compatibility'],
            suggested_brand='JAC',
            suggested_brand_id=self.jac.pk,
            suggested_category='Фильтры',
            suggested_category_id=self.category.pk,
            suggested_compatibility='JAC S5',
        )
        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, [row])
            self._run(preview, [product.pk], apply=True, report_dir=tmp)
        product.refresh_from_db()
        self.assertEqual(product.brand_id, self.jac.pk)
        self.assertEqual(product.selected_brands.count(), 0)
        self.assertEqual(product.selected_models.count(), 0)
        self.assertEqual(
            (
                Brand.objects.count(),
                Category.objects.count(),
                CarModel.objects.count(),
            ),
            counts,
        )

    def test_missing_brand_is_skipped_not_created(self):
        product = _product(
            title='Фильтр Lifan',
            article='APPLY-E',
            slug='apply-e',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=self.category,
            compatibility='Lifan 320',
        )
        row = _snapshot_row(
            product,
            approved_fields=['brand'],
            suggested_brand='GhostBrandXYZ',
            suggested_brand_id=None,
            dictionary_additions={'brands': ['GhostBrandXYZ'], 'categories': []},
            fields={'brand_name': 'GhostBrandXYZ', 'brand_id': None},
        )
        counts = Brand.objects.count()
        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, [row])
            outcome = self._run(preview, [product.pk], apply=True, report_dir=tmp)
        product.refresh_from_db()
        self.assertEqual(outcome['results'][0]['status'], STATUS_ERROR)
        self.assertIsNone(product.brand_id)
        self.assertEqual(Brand.objects.count(), counts)
        self.assertFalse(Brand.objects.filter(name='GhostBrandXYZ').exists())

    def test_article_mismatch_is_skipped(self):
        product = _product(
            title='Фильтр',
            article='APPLY-F',
            slug='apply-f',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            compatibility='',
        )
        row = _snapshot_row(
            product,
            current_article='OTHER-ARTICLE',
            approved_fields=['compatibility'],
            suggested_compatibility='JAC S5',
        )
        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, [row])
            outcome = self._run(preview, [product.pk], apply=True, report_dir=tmp)
        product.refresh_from_db()
        self.assertEqual(outcome['results'][0]['status'], STATUS_ERROR)
        self.assertIn('article mismatch', outcome['results'][0]['errors'][0])
        self.assertEqual(product.compatibility, '')
        self.assertEqual(product.article, 'APPLY-F')

    def test_changed_database_value_is_stale(self):
        product = _product(
            title='Фильтр воздушный',
            article='APPLY-G',
            slug='apply-g',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            compatibility='Jetour X70',
        )
        row = _snapshot_row(
            product,
            approved_fields=['compatibility'],
            suggested_compatibility='Jetour X70, X70 Plus',
        )
        product.compatibility = 'Ручная правка после preview'
        product.save(update_fields=['compatibility'])
        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, [row])
            outcome = self._run(preview, [product.pk], apply=True, report_dir=tmp)
        product.refresh_from_db()
        self.assertEqual(outcome['results'][0]['status'], STATUS_STALE)
        self.assertEqual(product.compatibility, 'Ручная правка после preview')

    def test_second_apply_is_already_applied(self):
        product = _product(
            title='Фильтр',
            article='APPLY-H',
            slug='apply-h',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            compatibility='Jetour X70',
        )
        row = _snapshot_row(
            product,
            approved_fields=['compatibility'],
            suggested_compatibility='Jetour X70, X70 Plus',
            fields={'compatibility': 'Jetour X70, X70 Plus'},
        )
        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, [row])
            first = self._run(preview, [product.pk], apply=True, report_dir=tmp)
            self.assertEqual(first['results'][0]['status'], STATUS_CHANGED)
            product.refresh_from_db()
            self.assertEqual(product.compatibility, 'Jetour X70, X70 Plus')
            second = self._run(preview, [product.pk], apply=True, report_dir=tmp)
        product.refresh_from_db()
        self.assertEqual(second['results'][0]['status'], STATUS_ALREADY_APPLIED)
        self.assertEqual(second['summary']['changed'], 0)
        self.assertEqual(product.compatibility, 'Jetour X70, X70 Plus')

    def test_research_notes_do_not_leak_into_product(self):
        product = _product(
            title='Фильтр',
            article='APPLY-I',
            slug='apply-i',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            compatibility='JAC S5',
            description='Старое описание',
        )
        dirty = 'ChatGPT / Gemini. По данным поставщика, в справочнике ZPT нет.'
        row = _snapshot_row(
            product,
            approved_fields=['title', 'description', 'compatibility'],
            suggested_title='Фильтр воздушный JAC S5',
            suggested_description=dirty,
            suggested_compatibility='JAC S5; отвергнут CS85 у поставщиков',
            research_notes=[{'text': dirty, 'severity': 'warning'}],
            evidence_notes=[{'text': 'FitInPart отвергнут', 'severity': 'info'}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, [row])
            outcome = self._run(preview, [product.pk], apply=True, report_dir=tmp)
        product.refresh_from_db()
        self.assertEqual(product.title, 'Фильтр воздушный JAC S5')
        self.assertEqual(product.description, 'Старое описание')
        self.assertEqual(product.compatibility, 'JAC S5')
        blob = ' '.join([
            product.title,
            product.description,
            product.compatibility,
            product.engine_compatibility,
            product.oem_cross_references,
        ])
        self.assertFalse(detect_internal_research_text(blob))
        self.assertIn('description', ' '.join(outcome['results'][0]['errors']))
        self.assertIn('compatibility', ' '.join(outcome['results'][0]['errors']))

    def test_unexpected_failure_rolls_back_transaction(self):
        first = _product(
            title='Первый',
            article='APPLY-J1',
            slug='apply-j1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            compatibility='',
        )
        second = _product(
            title='Второй',
            article='APPLY-J2',
            slug='apply-j2',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            compatibility='',
            whatsapp_number='77770000912',
        )
        rows = [
            _snapshot_row(
                first,
                approved_fields=['compatibility'],
                suggested_compatibility='JAC S5',
            ),
            _snapshot_row(
                second,
                approved_fields=['compatibility'],
                suggested_compatibility='Chery Tiggo 7',
            ),
        ]
        real_save = Product.save
        state = {'n': 0}

        def boom(self, *args, **kwargs):
            state['n'] += 1
            if state['n'] >= 2:
                raise RuntimeError('boom')
            return real_save(self, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            preview = _write_snapshot(tmp, rows)
            with patch.object(Product, 'save', boom):
                with self.assertRaises(RuntimeError):
                    self._run(
                        preview,
                        [first.pk, second.pk],
                        apply=True,
                        report_dir=tmp,
                    )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.compatibility, '')
        self.assertEqual(second.compatibility, '')
        self.assertEqual(first.title, 'Первый')
        self.assertEqual(second.title, 'Второй')
