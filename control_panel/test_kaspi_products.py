from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.models import (
    Category,
    KaspiOrder,
    KaspiSalesOperation,
    Product,
    ProductBarcode,
    ProductKaspiListing,
    ProductWarehouseStock,
    SellerProfile,
    Warehouse,
)
from catalog.warehouses import WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2
from repricer.models import KaspiCompetitorOfferSnapshot


URL = '/control/kaspi/products/'


def _staff():
    user = User.objects.create_user(
        username='kaspi-workbench-staff',
        password='secret-pass',
        is_staff=True,
    )
    return user, 'secret-pass'


def _seller(username='kaspi-wb-seller'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=username,
        phone='77070000001',
        city='Алматы',
    )


def _product(seller, article, **kwargs):
    defaults = {
        'title': f'Title {article}',
        'article': article,
        'seller_name': seller.name,
        'whatsapp_number': seller.phone,
        'seller_profile': seller,
        'city': seller.city,
        'status': 'active',
        'price': 5000,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _listing(product, master_sku, **kwargs):
    defaults = {
        'master_sku': master_sku,
        'merchant_sku': product.article,
        'is_active': True,
    }
    defaults.update(kwargs)
    return ProductKaspiListing.objects.create(product=product, **defaults)


def _stock(product, code, quantity):
    warehouse = Warehouse.objects.get(code=code)
    return ProductWarehouseStock.objects.create(
        product=product,
        warehouse=warehouse,
        quantity=quantity,
    )


def _order(seller):
    return KaspiOrder.objects.create(
        seller_profile=seller,
        external_order_id=f'RRN-{uuid4().hex[:10]}',
    )


def _op(seller, order, product, listing, **kwargs):
    qty = kwargs.get('quantity', 1)
    gross = kwargs.get('gross_amount', Decimal('2000.00'))
    defaults = {
        'seller_profile': seller,
        'order': order,
        'product': product,
        'listing': listing,
        'operation_type': KaspiSalesOperation.OperationType.PURCHASE,
        'operation_at': timezone.now(),
        'gross_amount': gross,
        'quantity': qty,
        'unit_gross_amount': abs(gross) / Decimal(qty),
        'match_status': KaspiSalesOperation.MatchStatus.LISTING_MATCHED,
        'source_filename': 'wb.csv',
        'source_sha256': 'abc',
        'source_fingerprint': uuid4().hex,
        'details': 'item',
    }
    defaults.update(kwargs)
    return KaspiSalesOperation.objects.create(**defaults)


def _snapshot():
    return {
        'products': list(
            Product.objects.order_by('pk').values_list(
                'pk', 'price', 'cost_price', 'stock_qty', 'status'
            )
        ),
        'listings': list(
            ProductKaspiListing.objects.order_by('pk').values_list(
                'pk', 'last_known_our_price', 'last_known_kaspi_qty', 'last_synced_at', 'public_url'
            )
        ),
        'stocks': list(
            ProductWarehouseStock.objects.order_by('pk').values_list(
                'pk', 'quantity'
            )
        ),
        'operations': KaspiSalesOperation.objects.count(),
        'orders': KaspiOrder.objects.count(),
        'competitor_snapshots': list(
            KaspiCompetitorOfferSnapshot.objects.order_by('pk').values_list(
                'pk', 'listing_id', 'seller_code', 'price', 'captured_at'
            )
        ),
    }


@override_settings(ALLOWED_HOSTS=['*'], ROOT_URLCONF='backend.urls')
class KaspiProductsControlTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST='zpt.kz')
        self.staff, self.password = _staff()
        self.seller = _seller()

    def _login(self):
        self.client.login(username=self.staff.username, password=self.password)

    def test_unauthenticated_cannot_access(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_staff_gets_200(self):
        self._login()
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Товары и цены')
        self.assertContains(response, 'ZPT.KZ Control')

    def test_product_list_renders(self):
        self._login()
        product = _product(self.seller, 'ART-100')
        response = self.client.get(URL)
        self.assertContains(response, 'ART-100')
        self.assertContains(response, product.title)

    def test_single_listing_price_renders(self):
        self._login()
        product = _product(self.seller, 'ART-PRICE')
        _listing(product, '1001', last_known_our_price=6500, last_known_kaspi_qty=4)
        response = self.client.get(URL)
        self.assertContains(response, '6 500')
        self.assertContains(response, '₸')
        self.assertContains(response, '4')

    def test_multiple_listings_do_not_aggregate_price(self):
        self._login()
        product = _product(self.seller, 'ART-MULTI')
        _listing(product, '2001', last_known_our_price=5000, last_known_kaspi_qty=10)
        _listing(product, '2002', last_known_our_price=6000, last_known_kaspi_qty=20)
        response = self.client.get(URL)
        self.assertContains(response, 'Несколько')
        self.assertNotContains(response, '11000')
        self.assertNotContains(response, '>30<')

    def test_pp1_and_pp2_render_separately(self):
        self._login()
        product = _product(self.seller, 'ART-STOCK')
        _stock(product, WAREHOUSE_CODE_PP1, 12)
        _stock(product, WAREHOUSE_CODE_PP2, 31)
        _listing(product, '3001', last_known_our_price=1000, last_known_kaspi_qty=29)
        response = self.client.get(URL)
        html = response.content.decode()
        self.assertIn('12', html)
        self.assertIn('31', html)
        self.assertIn('29', html)
        self.assertContains(response, '-2')

    def test_kaspi_qty_not_replaced_with_pp2(self):
        self._login()
        product = _product(self.seller, 'ART-QTY')
        _stock(product, WAREHOUSE_CODE_PP2, 8)
        _listing(product, '4001', last_known_kaspi_qty=3)
        response = self.client.get(URL)
        self.assertContains(response, '8')
        self.assertContains(response, '3')

    def test_multiple_listing_qty_not_summed(self):
        self._login()
        product = _product(self.seller, 'ART-QTY-MULTI')
        _listing(product, '5001', last_known_kaspi_qty=10)
        _listing(product, '5002', last_known_kaspi_qty=20)
        response = self.client.get(URL)
        self.assertContains(response, 'Несколько')
        self.assertContains(response, '10')
        self.assertContains(response, '20')
        self.assertNotContains(response, '11000')

    def test_sales_windows_and_returns(self):
        self._login()
        product = _product(self.seller, 'ART-SALES')
        listing = _listing(product, '6001', last_known_our_price=2000)
        order = _order(self.seller)
        _op(
            self.seller,
            order,
            product,
            listing,
            quantity=5,
            operation_at=timezone.now() - timedelta(days=2),
        )
        _op(
            self.seller,
            order,
            product,
            listing,
            quantity=4,
            operation_at=timezone.now() - timedelta(days=20),
        )
        _op(
            self.seller,
            order,
            product,
            listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            operation_at=timezone.now() - timedelta(days=1),
        )
        response = self.client.get(URL)
        self.assertContains(response, 'ART-SALES')
        html = response.content.decode()
        self.assertIn('>4<', html)
        self.assertIn('>8<', html)
        self.assertIn('>1<', html)
        self.assertContains(response, 'Возвраты')

    def test_no_listing_and_no_warehouse_and_no_economics(self):
        self._login()
        _product(self.seller, 'ART-EMPTY')
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ART-EMPTY')
        self.assertContains(response, 'Нет Kaspi')
        self.assertContains(response, 'Экономика не настроена')

    def test_search_by_sku_and_barcode(self):
        self._login()
        visible = _product(self.seller, 'KEEP')
        _listing(visible, 'SKU-KEEP', merchant_sku='MERCH-KEEP')
        hidden = _product(self.seller, 'HIDE')
        _listing(hidden, 'SKU-HIDE')
        ProductBarcode.objects.create(product=visible, code='4601234567890')
        by_article = self.client.get(URL, {'q': 'KEEP'})
        self.assertContains(by_article, 'KEEP')
        self.assertNotContains(by_article, 'HIDE')
        by_sku = self.client.get(URL, {'q': 'SKU-KEEP'})
        self.assertContains(by_sku, 'KEEP')
        self.assertNotContains(by_sku, 'HIDE')
        by_barcode = self.client.get(URL, {'q': '4601234567890'})
        self.assertContains(by_barcode, 'KEEP')

    def test_category_chip_uses_existing_category(self):
        self._login()
        filters = Category.objects.create(name='Масляные фильтры')
        spark = Category.objects.create(name='Свечи зажигания')
        _product(self.seller, 'FILT', category=filters, title='Filter item')
        _product(self.seller, 'SPARK', category=spark, title='Spark item')
        response = self.client.get(URL, {'filter': 'filters'})
        self.assertContains(response, 'FILT')
        self.assertNotContains(response, 'SPARK')

    def test_query_count_is_bounded(self):
        self._login()
        for index in range(12):
            product = _product(self.seller, f'ART-{index:02d}')
            listing = _listing(
                product,
                f'7{index:03d}',
                last_known_our_price=4000 + index,
                last_known_kaspi_qty=index,
            )
            _stock(product, WAREHOUSE_CODE_PP2, index)
            _op(self.seller, _order(self.seller), product, listing, quantity=1)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(captured), 45)
        snapshot_sql = [
            query['sql']
            for query in captured.captured_queries
            if 'kaspicompetitoroffersnapshot' in query['sql'].lower()
        ]
        self.assertLessEqual(len(snapshot_sql), 2)

    def test_get_performs_zero_writes(self):
        self._login()
        product = _product(self.seller, 'ART-RO', cost_price=900)
        listing = _listing(
            product,
            '8001',
            last_known_our_price=4400,
            last_known_kaspi_qty=2,
        )
        _stock(product, WAREHOUSE_CODE_PP1, 5)
        _stock(product, WAREHOUSE_CODE_PP2, 2)
        _op(self.seller, _order(self.seller), product, listing)
        before = _snapshot()
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_snapshot(), before)

    def test_price_block_has_three_columns(self):
        self._login()
        product = _product(self.seller, 'ART-COLS')
        _listing(product, '129900001_555555555')
        response = self.client.get(URL)
        self.assertContains(response, 'Конкурент')
        self.assertContains(response, 'Рекомендация')
        self.assertContains(response, 'Данные конкурентов ещё не получены')
        self.assertContains(response, 'Нет снимка конкурентов')
        self.assertNotContains(response, 'Правило: не задано')
        self.assertContains(response, 'Мин. конкурент')
        self.assertContains(response, 'товаров')
        self.assertNotContains(response, 'Всего:')
        self.assertContains(response, 'cp-chevron')
        html = response.content.decode()
        econ_start = html.index('Экономика')
        econ_end = html.index('</section>', econ_start)
        self.assertIn('Экономика не настроена', html[econ_start:econ_end])
        self.assertNotIn('Открыть ZPT', html[econ_start:econ_end])
        self.assertIn('cp-expand-actions', html)
        self.assertIn('Открыть ZPT', html)

    def test_sort_links_live_in_headers(self):
        self._login()
        _product(self.seller, 'ART-SORT')
        response = self.client.get(URL)
        html = response.content.decode()
        self.assertIn('sort=article', html)
        self.assertIn('sort=price', html)
        self.assertIn('sort=pp2', html)
        self.assertIn('sort=sales_30', html)
        self.assertIn('cp-th-sort', html)

    def test_placeholder_markup_and_primary_status(self):
        self._login()
        _product(self.seller, 'ART-PH')
        response = self.client.get(URL)
        self.assertContains(response, 'images/product-placeholder.svg')
        self.assertContains(response, 'cp-thumb-wrap')
        self.assertContains(response, 'data-fallback')
        self.assertContains(response, 'Нет Kaspi')

    def test_zpt_link_uses_get_absolute_url_and_opens_safely(self):
        self._login()
        product = _product(self.seller, 'ART-ZPT', slug='art-zpt-public')
        zpt_url = product.get_absolute_url()
        self.assertTrue(zpt_url)
        self.assertIn(product.slug, zpt_url)
        response = self.client.get(URL)
        html = response.content.decode()
        self.assertIn(zpt_url, html)
        self.assertIn(f'href="{zpt_url}"', html)
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn('Открыть ZPT ↗', html)
        self.assertIn('class="cp-article cp-ext-link"', html)
        js = (
            Path(__file__).resolve().parent / 'static' / 'control_panel' / 'control.js'
        ).read_text(encoding='utf-8')
        self.assertIn("event.target.closest('a, button')", js)
        self.assertIn('stopPropagation', js)

    def test_single_listing_kaspi_link_only_when_public_url_set(self):
        self._login()
        url = 'https://kaspi.kz/shop/p/filtr-maslianyi-901-102-111/'
        with_link = _product(self.seller, 'ART-K-YES')
        _listing(with_link, 'SKU-YES', public_url=url)
        without = _product(self.seller, 'ART-K-NO')
        _listing(without, 'SKU-NO')
        response = self.client.get(URL)
        html = response.content.decode()
        self.assertIn(url, html)
        self.assertIn('cp-kaspi-link', html)
        self.assertIn('Kaspi ↗', html)
        self.assertIn('Открыть Kaspi ↗', html)
        self.assertNotIn(f'https://kaspi.kz/shop/p/{without.kaspi_listings.get().master_sku}', html)
        no_link_html = html
        self.assertIn('ART-K-NO', no_link_html)
        self.assertEqual(no_link_html.count('cp-kaspi-link'), 1)

    def test_multiple_listings_keep_per_listing_kaspi_links(self):
        self._login()
        url_a = 'https://kaspi.kz/shop/p/item-alpha-111/'
        url_b = 'https://kaspi.kz/shop/p/item-beta-222/'
        product = _product(self.seller, 'ART-K-MULTI')
        _listing(product, 'SKU-A', public_url=url_a)
        _listing(product, 'SKU-B', public_url=url_b)
        response = self.client.get(URL)
        html = response.content.decode()
        self.assertIn(url_a, html)
        self.assertIn(url_b, html)
        self.assertNotEqual(url_a, url_b)
        self.assertNotIn('cp-kaspi-link', html)
        actions_start = html.index('cp-expand-actions')
        actions_end = html.index('</div>', actions_start)
        self.assertNotIn('Открыть Kaspi', html[actions_start:actions_end])
        self.assertEqual(html.count('Открыть Kaspi ↗'), 2)

    def _competitor_offer(self, listing, *, seller_name, price, seller_code='', minutes_ago=0):
        return KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing,
            seller_name=seller_name,
            seller_code=seller_code,
            price=Decimal(str(price)),
            is_available=True,
            source='kaspi_public',
            captured_at=timezone.now() - timedelta(minutes=minutes_ago),
        )

    @override_settings(
        KASPI_OWN_MERCHANT_IDS='TEST-OWN',
        KASPI_OWN_MERCHANT_NAMES='TEST-MERCHANT',
        KASPI_REPRICER_UNDERCUT_AMOUNT=300,
    )
    def test_competitor_cell_shows_latest_other_price(self):
        self._login()
        product = _product(self.seller, 'ART-COMP-A')
        listing = _listing(product, '115801437_271928151', last_known_our_price=3034)
        now_batch = timezone.now()
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing,
            seller_name='TEST-MERCHANT',
            seller_code='TEST-OWN',
            price=Decimal('3034'),
            source='kaspi_public',
            captured_at=now_batch,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing,
            seller_name='ИП Other',
            seller_code='30308762',
            price=Decimal('3033'),
            source='kaspi_public',
            captured_at=now_batch,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing,
            seller_name='Old cheap',
            seller_code='OLD',
            price=Decimal('1000'),
            source='kaspi_public',
            captured_at=timezone.now() - timedelta(days=1),
        )
        response = self.client.get(URL)
        html = response.content.decode()
        self.assertIn('3 033', html)
        self.assertIn('ИП Other', html)
        self.assertNotIn('1 000 ₸', html)
        row_start = html.index('ART-COMP-A')
        row_end = html.index('id="kaspi-', row_start)
        main_row = html[row_start:row_end]
        self.assertIn('3 033', main_row)
        self.assertIn('2 733', html)
        self.assertIn('−300', html)

    @override_settings(KASPI_OWN_MERCHANT_IDS='TEST-OWN', KASPI_OWN_MERCHANT_NAMES='TEST-MERCHANT')
    def test_competitor_cell_no_other_offers(self):
        self._login()
        product = _product(self.seller, 'ART-COMP-B')
        listing = _listing(product, '136510902_627349511', last_known_our_price=3410)
        self._competitor_offer(
            listing,
            seller_name='TEST-MERCHANT',
            seller_code='TEST-OWN',
            price=3410,
        )
        response = self.client.get(URL)
        self.assertContains(response, 'Нет других')
        html = response.content.decode()
        kaspi_start = html.index('ART-COMP-B')
        self.assertIn('Нет других', html[kaspi_start:])
        rec_cell = html[kaspi_start:html.index('id="kaspi-', kaspi_start)]
        self.assertIn('—', rec_cell)

    def test_unresolved_mapping_cell(self):
        self._login()
        product = _product(self.seller, 'X01-90000014')
        _listing(product, 'X01-90000014', last_known_our_price=1000)
        response = self.client.get(URL)
        self.assertContains(response, 'Не сопоставлен Kaspi ID')
        self.assertContains(response, 'Нет надёжного числового Kaspi product id')

    def test_numeric_article_as_master_sku_is_unresolved(self):
        self._login()
        product = _product(self.seller, '8890649934')
        _listing(product, '8890649934', last_known_our_price=1000)
        response = self.client.get(URL)
        self.assertContains(response, 'Не сопоставлен Kaspi ID')

    def test_plain_active_sku_different_from_article_is_unresolved(self):
        self._login()
        product = _product(self.seller, '272774M400')
        _listing(product, '126807700', last_known_our_price=2250)
        response = self.client.get(URL)
        self.assertContains(response, 'Не сопоставлен Kaspi ID')
        self.assertContains(response, 'Нет надёжного числового Kaspi product id')

    def test_oem_article_with_bound_public_url_is_not_unresolved(self):
        self._login()
        product = _product(self.seller, '272774M400')
        _listing(
            product,
            '126807700',
            last_known_our_price=2250,
            public_url='https://kaspi.kz/shop/p/filtr-vozdushnyi-272774m400-987654321/',
        )
        response = self.client.get(URL)
        self.assertContains(response, '272774M400')
        self.assertNotContains(response, 'Не сопоставлен Kaspi ID')
        self.assertContains(response, 'Данные конкурентов ещё не получены')


    @override_settings(
        KASPI_OWN_MERCHANT_IDS='TEST-OWN',
        KASPI_COMPETITOR_FRESH_MINUTES=180,
    )
    def test_competitor_cell_stale_is_marked(self):
        self._login()
        product = _product(self.seller, 'ART-COMP-STALE')
        listing = _listing(product, '116207063_792647100', last_known_our_price=1150)
        self._competitor_offer(
            listing,
            seller_name='Other',
            seller_code='30327411',
            price=1954,
            minutes_ago=200,
        )
        response = self.client.get(URL)
        html = response.content.decode()
        self.assertIn('1 954', html)
        self.assertIn('устарело', html)
        self.assertIn('is-stale', html)
        self.assertNotIn('1 654', html)

    @override_settings(KASPI_OWN_MERCHANT_IDS='', KASPI_OWN_MERCHANT_NAMES='')
    def test_competitor_fail_closed_without_own_merchant(self):
        self._login()
        product = _product(self.seller, 'ART-COMP-CFG')
        listing = _listing(product, '129914457_677517150', last_known_our_price=3740)
        now_batch = timezone.now()
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing,
            seller_name='TEST-MERCHANT',
            seller_code='TEST-OWN',
            price=Decimal('3740'),
            source='kaspi_public',
            captured_at=now_batch,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing,
            seller_name='AMIOSPHY GROUP',
            seller_code='30440420',
            price=Decimal('6864'),
            source='kaspi_public',
            captured_at=now_batch,
        )
        response = self.client.get(URL)
        html = response.content.decode()
        self.assertIn('Не настроен собственный продавец Kaspi', html)
        self.assertNotIn('6 864', html)
        self.assertContains(response, 'title="Не настроен собственный продавец Kaspi"')

    @override_settings(KASPI_OWN_MERCHANT_IDS='TEST-OWN')
    def test_multiple_listings_do_not_aggregate_competitors(self):
        self._login()
        product = _product(self.seller, 'ART-COMP-MULTI')
        first = _listing(product, '111111111_1001', last_known_our_price=3034)
        second = _listing(product, '222222222_2002', last_known_our_price=3740)
        now_batch = timezone.now()
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=first,
            seller_name='Cheap',
            seller_code='C1',
            price=Decimal('1954'),
            source='kaspi_public',
            captured_at=now_batch,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=second,
            seller_name='Dear',
            seller_code='C2',
            price=Decimal('6864'),
            source='kaspi_public',
            captured_at=now_batch,
        )
        response = self.client.get(URL)
        html = response.content.decode()
        main_start = html.index('ART-COMP-MULTI')
        expand_id = html.index(f'id="kaspi-{product.pk}"', main_start)
        main_row = html[main_start:expand_id]
        self.assertIn('Несколько', main_row)
        self.assertNotIn('1 954', main_row)
        self.assertNotIn('6 864', main_row)
        expand = html[expand_id:]
        self.assertIn('1 954', expand)
        self.assertIn('6 864', expand)
        self.assertIn('Cheap', expand)
        self.assertIn('Dear', expand)

    @override_settings(KASPI_OWN_MERCHANT_IDS='TEST-OWN')
    def test_competitor_query_count_stays_bounded_with_snapshots(self):
        self._login()
        for index in range(12):
            product = _product(self.seller, f'ART-CQ-{index:02d}')
            listing = _listing(
                product,
                f'9{index:03d}_8{index:03d}',
                last_known_our_price=4000 + index,
                last_known_kaspi_qty=index,
            )
            self._competitor_offer(
                listing,
                seller_name='Other',
                seller_code=f'S{index}',
                price=3000 + index,
            )
            self._competitor_offer(
                listing,
                seller_name='Old',
                seller_code=f'O{index}',
                price=1000,
                minutes_ago=24 * 60,
            )
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '3 000')
        self.assertNotContains(response, '1 000 ₸')
        self.assertLess(len(captured), 45)
        snapshot_sql = [
            query['sql']
            for query in captured.captured_queries
            if 'kaspicompetitoroffersnapshot' in query['sql'].lower()
        ]
        self.assertLessEqual(len(snapshot_sql), 2)
