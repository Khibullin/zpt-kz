"""Public catalog search used by the ZPT Гид assistant."""
from __future__ import annotations

from django.test import TestCase, override_settings
from unittest.mock import patch

from catalog.guide_catalog_search import STOCK_UNKNOWN_LABEL, search_public_catalog
from catalog.models import Product, ProductWarehouseStock, Warehouse
from catalog.warehouses import WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2
from catalog.warehouse_stock_sync import ensure_default_warehouses


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 2500,
        'seller_name': 'AG Parts',
        'whatsapp_number': '77019990000',
        'status': 'active',
        'article': 'ABC-001',
        'cost_price': 900,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class GuideCatalogSearchTests(TestCase):
    def test_exact_article_is_first_and_keeps_leading_zeros(self):
        exact = _product(
            title='Датчик с нулями',
            article='012345',
            slug='sensor-zeros',
            price=3300,
        )
        _product(
            title='Похожий датчик 012345 в названии',
            article='ZZ-999',
            slug='sensor-name',
            price=1100,
        )
        result = search_public_catalog('012345')
        self.assertTrue(result['ok'])
        self.assertFalse(result['not_found'])
        self.assertEqual(result['results'][0]['article'], '012345')
        self.assertEqual(result['results'][0]['match_kind'], 'article_exact')
        self.assertEqual(result['results'][0]['title'], exact.title)
        self.assertEqual(result['results'][0]['price'], 3300)
        dumped = str(result)
        self.assertNotIn('900', dumped)
        self.assertNotIn('77019990000', dumped)
        self.assertNotIn('cost_price', dumped)

    def test_title_search_and_hidden_products_are_excluded(self):
        _product(
            title='Колодки тормозные передние',
            article='BRK-1',
            slug='brake-pads',
            price=8000,
        )
        _product(
            title='Колодки тормозные скрытые',
            article='BRK-HIDDEN',
            slug='brake-hidden',
            status='hidden',
            price=1,
        )
        result = search_public_catalog('тормозные')
        self.assertTrue(result['ok'])
        articles = [item['article'] for item in result['results']]
        self.assertIn('BRK-1', articles)
        self.assertNotIn('BRK-HIDDEN', articles)

    def test_not_found_is_not_existence_claim(self):
        result = search_public_catalog('NO-SUCH-SKU-999')
        self.assertTrue(result['ok'])
        self.assertTrue(result['not_found'])
        self.assertEqual(result['results'], [])
        self.assertEqual(result['message'], 'В каталоге ZPT не найден')

    def test_unknown_stock_does_not_use_pp1_or_pp2(self):
        ensure_default_warehouses()
        product = _product(
            title='Фильтр без публичного остатка',
            article='FLT-0',
            slug='filter-unknown',
            stock_qty=None,
        )
        pp1 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP1)
        pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)
        ProductWarehouseStock.objects.create(product=product, warehouse=pp1, quantity=12)
        ProductWarehouseStock.objects.create(product=product, warehouse=pp2, quantity=4)
        result = search_public_catalog('FLT-0')
        self.assertTrue(result['ok'])
        self.assertEqual(result['results'][0]['availability'], STOCK_UNKNOWN_LABEL)
        self.assertNotIn('16', str(result))
        self.assertNotIn('PP1', str(result))
        self.assertNotIn('PP2', str(result))

    def test_search_error_is_not_not_found(self):
        with patch(
            'catalog.guide_catalog_search._rank_products',
            side_effect=RuntimeError('db down'),
        ):
            result = search_public_catalog('колодки')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'unavailable')
        self.assertNotIn('not_found', result)

    @override_settings(PUBLIC_BASE_URL='https://zpt.kz')
    def test_price_on_request_and_public_url(self):
        product = _product(
            title='Ремень',
            article='BELT-7',
            slug='belt-7',
            price=None,
            price_on_request=True,
        )
        result = search_public_catalog('BELT-7')
        item = result['results'][0]
        self.assertIsNone(item['price'])
        self.assertTrue(item['price_on_request'])
        self.assertTrue(item['url'].startswith('https://zpt.kz/'))
        self.assertIn(product.slug, item['url'])
