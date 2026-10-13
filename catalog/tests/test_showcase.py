"""Behavioural coverage for the owner-approved homepage merchandising rules."""
from collections import Counter
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.models import Product, ProductKaspiListing, SellerProfile
from catalog.product_display import CHINESE_PRODUCT_TITLES_RU, public_product_title
from catalog.showcase import (
    SESSION_KEY, build_showcase_order, card_quality, category_key,
    homepage_showcase_page, seller_key,
)
from catalog.views import attach_sellers_to_products
from catalog.templatetags.product_extras import public_card_title

NOW = datetime(2026, 10, 13, 5, 20, tzinfo=dt_timezone.utc)


class ShowcaseTests(TestCase):
    def product(self, seller=0, title='Масляный фильтр', **kwargs):
        values = dict(title=title, seller_name=f'Shop {seller}',
                      whatsapp_number=f'+770012340{seller:02d}', price=1500,
                      main_image='products/real.webp', status='active')
        values.update(kwargs)
        return Product.objects.create(**values)

    def candidates(self):
        return attach_sellers_to_products(Product.objects.filter(status='active')
            .select_related('seller_profile').prefetch_related('kaspi_listings'))

    def catalogue(self):
        for seller in range(8):
            for title in ('Масляный фильтр', 'Свеча зажигания', 'Тормозные колодки',
                          'Задний амортизатор', 'Передний бампер', 'Поршень'):
                self.product(seller, title)

    def test_dominant_import_cannot_fill_first_twelve(self):
        self.catalogue()
        for _ in range(30):
            self.product(0, 'Передний бампер')
        candidates = self.candidates()
        mapping = {p.pk: p for p in candidates}
        order = build_showcase_order(candidates, 100)
        first = [mapping[pk] for pk in order[:12]]
        sellers = [seller_key(p) for p in first]
        self.assertGreaterEqual(len(set(sellers)), 6)
        self.assertLessEqual(max(Counter(sellers).values()), 2)
        self.assertTrue(all(a != b for a, b in zip(sellers, sellers[1:])))
        categories = [category_key(p) for p in first]
        self.assertGreaterEqual(len(set(categories)), 5)
        self.assertTrue(all(a != b for a, b in zip(categories, categories[1:])))
        self.assertEqual(set(order), set(mapping))
        self.assertEqual(len(order), len(mapping))

    def test_small_seller_pool_fills_and_terminates_without_duplicates(self):
        for _ in range(17):
            self.product()
        order = build_showcase_order(self.candidates(), 100)
        self.assertEqual(len(order), 17)
        self.assertEqual(len(set(order)), 17)

    def test_hour_changes_order_and_gives_older_items_space(self):
        self.catalogue()
        candidates = self.candidates()
        first = build_showcase_order(candidates, 100)
        self.assertEqual(first, build_showcase_order(candidates, 100))
        self.assertNotEqual(first[:12], build_showcase_order(candidates, 101)[:12])
        newest = {p.pk for p in sorted(candidates, key=lambda p:p.pk, reverse=True)[:12]}
        self.assertTrue(set(first[:12]) & newest)
        self.assertTrue(set(first[:12]) - newest)

    def test_clear_priced_photographed_cards_outrank_unfinished_same_category(self):
        good = [self.product() for _ in range(5)]
        poor = [self.product(main_image='', price=None, price_on_request=True,
                             title='未知滤芯') for _ in range(5)]
        # Same actual category makes this a presentation quality comparison.
        with patch('catalog.showcase.category_key', return_value='filters'):
            order = build_showcase_order(self.candidates(), 100)
        self.assertIn(order[0], {p.pk for p in good})
        self.assertEqual(set(order), {p.pk for p in good + poor})

    def test_canonical_seller_and_legacy_identity_share_the_same_quota(self):
        seller = SellerProfile.objects.create(user=get_user_model().objects.create_user('feed-shop'),
            name='Shop', phone='+77001234000', slug='feed-shop')
        explicit = self.product(4, seller_profile=seller)
        legacy = self.product(0, seller_name='Old name')
        candidates = {p.pk: p for p in self.candidates()}
        self.assertEqual(seller_key(candidates[explicit.pk]), seller_key(candidates[legacy.pk]))

    def test_kaspi_rank_uses_verified_link_independent_of_local_zero_stock(self):
        seller = SellerProfile.objects.create(user=get_user_model().objects.create_user('feed-ag'),
            name='AG Parts', phone='+77001234000', slug='ag-parts')
        product = self.product(seller_profile=seller, stock_qty=0)
        score_before = card_quality(self.candidates()[0])
        ProductKaspiListing.objects.create(product=product, master_sku='12345678',
            public_url='https://kaspi.kz/shop/p/test-filter-12345678/')
        score_after = card_quality(self.candidates()[0])
        self.assertGreater(score_after, score_before)
        product.refresh_from_db()
        self.assertEqual(product.stock_qty, 0)

    def test_snapshot_survives_hour_insert_hide_and_price_change(self):
        self.catalogue()
        with patch('catalog.showcase.timezone.now', return_value=NOW):
            first = self.client.get('/')
        token = first.context['showcase_snapshot']
        ids = self.client.session[SESSION_KEY][token]
        shown = {p.pk for p in first.context['products']}
        Product.objects.filter(pk=ids[0]).update(status='hidden')
        Product.objects.filter(pk=ids[12]).update(price=7777)
        self.product(9, 'Новая фара')
        with patch('catalog.showcase.timezone.now', return_value=NOW + timedelta(hours=1)):
            second = self.client.get('/', {'showcase_page': 2, 'showcase': token})
            fresh = self.client.get('/')
        self.assertEqual([p.pk for p in second.context['products']], ids[12:24])
        self.assertFalse(shown & {p.pk for p in second.context['products']})
        self.assertEqual(second.context['products'][0].price, 7777)
        self.assertNotEqual(fresh.context['showcase_snapshot'], token)
        self.assertNotEqual([p.pk for p in fresh.context['products']], ids[:12])
        self.assertContains(second, f'&amp;showcase={token}')

    def test_withdrawn_page_items_leave_gaps_without_shifting_next_page(self):
        self.catalogue()
        first = self.client.get('/')
        token = first.context['showcase_snapshot']
        ids = self.client.session[SESSION_KEY][token]
        Product.objects.filter(pk__in=ids[12:24]).update(status='hidden')
        second = self.client.get('/', {'showcase_page': 2, 'showcase': token})
        third = self.client.get('/', {'showcase_page': 3, 'showcase': token})
        self.assertEqual(second.context['products'], [])
        self.assertEqual([p.pk for p in third.context['products']], ids[24:36])
        self.assertContains(second, 'id="showcase-more"')

    def test_catalog_search_order_and_unpublished_visibility_are_unchanged(self):
        one = self.product(title='Масляный фильтр A')
        two = self.product(title='Масляный фильтр B')
        self.product(title='Hidden filter', status='hidden')
        response = self.client.get('/', {'q': 'Масляный фильтр'})
        self.assertEqual([p.pk for p in response.context['products']], [two.pk, one.pk])
        self.assertNotIn(SESSION_KEY, self.client.session)
        home = self.client.get('/')
        self.assertNotContains(home, 'Hidden filter')

    def test_invalid_snapshot_cannot_choose_arbitrary_product_ids(self):
        self.product()
        hidden = self.product(title='Secret draft', status='hidden')
        response = self.client.get('/', {'showcase': str(hidden.pk), 'showcase_page': 'bad'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Secret draft')

    def test_snapshot_preparation_has_bounded_query_count(self):
        self.catalogue()
        request = SimpleNamespace(GET={}, session={})
        with self.assertNumQueries(4):
            page, _ = homepage_showcase_page(request, Product.objects.filter(status='active'),
                                             attach_sellers_to_products)
        self.assertEqual(len(page.object_list), 12)


class ProductDisplayTests(TestCase):
    def test_all_reviewed_chinese_names_have_precise_russian_labels(self):
        for chinese, russian in CHINESE_PRODUCT_TITLES_RU.items():
            product = SimpleNamespace(title=chinese, article='OE-123')
            self.assertEqual(public_product_title(product), russian)
            self.assertEqual(public_card_title(product), russian)
            self.assertEqual(product.title, chinese)
        self.assertEqual(public_product_title(SimpleNamespace(title='前减震器（右）')),
                         'Передний правый амортизатор')

    def test_unknown_chinese_and_latin_codes_are_preserved(self):
        for title in ('待确认的产品', 'Фильтр JAC — ABC-123', 'F188107041'):
            self.assertEqual(public_product_title(SimpleNamespace(title=title)), title)
