import json
from datetime import timedelta

from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.commercial import get_request_seller_profile
from catalog.models import (
    Brand,
    CarModel,
    Country,
    Product,
    ProductConsignment,
    ProductPriceTier,
    ProductPromotion,
    SellerProfile,
)
from orders.cart import CartManager


COST_PRICE_MARKER = 87654321
RETAIL_PRICE = 1500
WHOLESALE_PRICES = (1200, 1050, 900, 800)


def _make_seller(username, name, phone):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
        city='Алматы',
    )


def _make_product(**kwargs):
    defaults = {
        'title': 'B2B тестовый товар',
        'price': RETAIL_PRICE,
        'seller_name': 'B2B Shop',
        'whatsapp_number': '+77771234567',
        'status': 'active',
        'city': 'Алматы',
        'article': '',
        'compatibility': 'Подходит для Camry 2018-2022',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _catalog_ids(response):
    return [product.pk for product in response.context['products']]


class SellerProfileHelperTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_anonymous_user_returns_none(self):
        request = self.factory.get('/market/')
        request.user = AnonymousUser()
        self.assertIsNone(get_request_seller_profile(request))

    def test_user_without_seller_profile_returns_none(self):
        user = User.objects.create_user(username='plain-user', password='secret12345')
        request = self.factory.get('/market/')
        request.user = user
        self.assertIsNone(get_request_seller_profile(request))

    def test_user_with_seller_profile_returns_profile(self):
        seller = _make_seller('helper-seller', 'Helper Shop', '77770000010')
        request = self.factory.get('/market/')
        request.user = seller.user
        self.assertEqual(get_request_seller_profile(request), seller)


class B2BVisibilityTests(TestCase):
    def setUp(self):
        self.seller = _make_seller('b2b-seller', 'B2B Shop', '77770000011')
        self.plain_user = User.objects.create_user(
            username='plain-buyer',
            password='secret12345',
        )
        self.product = _make_product(
            title='Фильтр масляный B2B',
            slug='oil-filter-b2b-visibility',
            article='B2B-VIS-1',
            cost_price=COST_PRICE_MARKER,
        )
        ProductPriceTier.objects.create(
            product=self.product,
            min_qty=10,
            price=WHOLESALE_PRICES[0],
        )
        ProductPriceTier.objects.create(
            product=self.product,
            min_qty=20,
            price=WHOLESALE_PRICES[1],
        )
        ProductPriceTier.objects.create(
            product=self.product,
            min_qty=30,
            price=WHOLESALE_PRICES[2],
        )
        ProductPriceTier.objects.create(
            product=self.product,
            min_qty=50,
            price=WHOLESALE_PRICES[3],
        )
        ProductConsignment.objects.create(
            product=self.product,
            enabled=True,
            max_qty=8,
            settlement_price=1100,
            term_days=14,
            conditions='Возврат непроданного остатка',
        )
        self.detail_url = reverse(
            'product_detail',
            kwargs={'slug': self.product.slug},
        )

    def _login_seller(self):
        self.client.login(username='b2b-seller', password='secret12345')

    def _assert_cost_price_hidden(self, response):
        self.assertNotContains(response, str(COST_PRICE_MARKER))
        self.assertNotContains(response, '87 654 321')
        self.assertNotContains(response, '87,654,321')
        self.assertNotContains(response, 'Себестоимость')
        self.assertNotContains(response, 'cost_price')

    def test_anonymous_user_does_not_see_wholesale_terms(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.title)
        self.assertContains(response, 'Купить')
        self.assertNotContains(response, 'Условия для продавцов')
        self.assertNotContains(response, 'Оптовые цены')
        self.assertNotContains(response, 'от 10 шт.')
        self.assertNotContains(response, 'ОПТ')
        self.assertNotContains(response, 'НА РЕАЛИЗАЦИЮ')
        self.assertNotContains(response, 'Можно взять на реализацию')
        self._assert_cost_price_hidden(response)

    def test_user_without_seller_profile_does_not_see_b2b_terms(self):
        self.client.login(username='plain-buyer', password='secret12345')
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Условия для продавцов')
        self.assertNotContains(response, 'Оптовые цены')
        self.assertNotContains(response, 'ОПТ')
        self.assertNotContains(response, 'Предложения для продавцов')
        self._assert_cost_price_hidden(response)

    def test_seller_profile_sees_wholesale_tiers(self):
        self._login_seller()
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Условия для продавцов')
        self.assertContains(response, 'Оптовые цены')
        self.assertContains(response, 'от 10 шт.')
        self.assertContains(response, 'от 20 шт.')
        self.assertContains(response, 'от 30 шт.')
        self.assertContains(response, 'от 50 шт.')
        html = response.content.decode()
        pos_10 = html.find('от 10 шт.')
        pos_20 = html.find('от 20 шт.')
        pos_30 = html.find('от 30 шт.')
        pos_50 = html.find('от 50 шт.')
        self.assertTrue(pos_10 < pos_20 < pos_30 < pos_50)

    def test_seller_profile_sees_consignment(self):
        self._login_seller()
        response = self.client.get(self.detail_url)
        self.assertContains(response, 'Можно взять на реализацию')
        self.assertContains(response, 'Максимум: 8 шт.')
        self.assertContains(response, 'Расчётная цена:')
        self.assertContains(response, 'Срок: 14 дн.')
        self.assertContains(response, 'Возврат непроданного остатка')
        self.assertContains(response, 'НА РЕАЛИЗАЦИЮ')

    def test_cost_price_is_not_shown_to_seller_either(self):
        self._login_seller()
        detail = self.client.get(self.detail_url)
        catalog = self.client.get(reverse('catalog_list'), {'all': '1'})
        self._assert_cost_price_hidden(detail)
        self._assert_cost_price_hidden(catalog)

    def test_retail_product_card_still_works(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.title)
        self.assertContains(response, 'Купить')
        self.assertContains(response, 'Перейти в корзину')
        self.assertContains(response, 'Подходит для Camry 2018-2022')
        self.assertNotContains(response, 'Купить оптом')
        self.assertNotContains(response, 'Взять на реализацию')
        self.assertNotContains(response, 'Купить по акции')


class B2BPromotionDisplayTests(TestCase):
    def setUp(self):
        self.seller = _make_seller('promo-seller', 'Promo Shop', '77770000012')
        self.now = timezone.now()
        self.product = _make_product(
            title='Амортизатор B2B promo',
            slug='shock-b2b-promo',
            article='B2B-PROMO-1',
            cost_price=COST_PRICE_MARKER,
        )
        self.detail_url = reverse(
            'product_detail',
            kwargs={'slug': self.product.slug},
        )
        self.client.login(username='promo-seller', password='secret12345')

    def test_active_sale_is_shown(self):
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=990,
            is_active=True,
            starts_at=self.now - timedelta(days=1),
            ends_at=self.now + timedelta(days=3),
            qty_limit=12,
        )
        response = self.client.get(self.detail_url)
        self.assertContains(response, 'Распродажа')
        self.assertContains(response, 'РАСПРОДАЖА')
        self.assertContains(response, 'Лимит: 12 шт.')
        self.assertNotContains(response, str(COST_PRICE_MARKER))

    def test_expired_sale_is_hidden(self):
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=880,
            is_active=True,
            starts_at=self.now - timedelta(days=10),
            ends_at=self.now - timedelta(days=1),
        )
        response = self.client.get(self.detail_url)
        self.assertNotContains(response, 'Распродажа')
        self.assertNotContains(response, 'РАСПРОДАЖА')
        self.assertNotContains(response, '880')

    def test_future_sale_is_hidden(self):
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=770,
            is_active=True,
            starts_at=self.now + timedelta(days=2),
            ends_at=self.now + timedelta(days=10),
        )
        response = self.client.get(self.detail_url)
        self.assertNotContains(response, 'Распродажа')
        self.assertNotContains(response, 'РАСПРОДАЖА')
        self.assertNotContains(response, '770')

    def test_active_promo_is_shown(self):
        ProductPromotion.objects.create(
            product=self.product,
            promotion_type=ProductPromotion.TYPE_PROMO,
            price=1110,
            is_active=True,
        )
        response = self.client.get(self.detail_url)
        self.assertContains(response, 'Акция')
        self.assertContains(response, 'АКЦИЯ')


class B2BOfferFilterTests(TestCase):
    def setUp(self):
        self.seller = _make_seller('filter-seller', 'Filter Shop', '77770000013')
        self.now = timezone.now()
        self.wholesale = _make_product(
            title='Товар опт UNIQUE-WH',
            slug='b2b-filter-wholesale',
            article='B2B-F-WH',
        )
        ProductPriceTier.objects.create(
            product=self.wholesale,
            min_qty=10,
            price=1200,
        )
        ProductPriceTier.objects.create(
            product=self.wholesale,
            min_qty=20,
            price=1050,
        )
        self.sale = _make_product(
            title='Товар распродажа UNIQUE-SALE',
            slug='b2b-filter-sale',
            article='B2B-F-SALE',
        )
        ProductPromotion.objects.create(
            product=self.sale,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=990,
            is_active=True,
            starts_at=self.now - timedelta(hours=1),
            ends_at=self.now + timedelta(days=2),
        )
        self.expired_sale = _make_product(
            title='Товар просроченная UNIQUE-EXP',
            slug='b2b-filter-expired-sale',
            article='B2B-F-EXP',
        )
        ProductPromotion.objects.create(
            product=self.expired_sale,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=500,
            is_active=True,
            starts_at=self.now - timedelta(days=5),
            ends_at=self.now - timedelta(hours=1),
        )
        self.promo = _make_product(
            title='Товар акция UNIQUE-PROMO',
            slug='b2b-filter-promo',
            article='B2B-F-PROMO',
        )
        ProductPromotion.objects.create(
            product=self.promo,
            promotion_type=ProductPromotion.TYPE_PROMO,
            price=1110,
            is_active=True,
        )
        self.consignment = _make_product(
            title='Товар реализация UNIQUE-CONS',
            slug='b2b-filter-consignment',
            article='B2B-F-CONS',
        )
        ProductConsignment.objects.create(
            product=self.consignment,
            enabled=True,
            max_qty=4,
            settlement_price=1300,
        )
        self.plain = _make_product(
            title='Обычный розничный UNIQUE-RETAIL',
            slug='b2b-filter-retail',
            article='B2B-F-RETAIL',
        )
        self.list_url = reverse('catalog_list')

    def _login_seller(self):
        self.client.login(username='filter-seller', password='secret12345')

    def _ids(self, offer):
        response = self.client.get(self.list_url, {'all': '1', 'offer': offer})
        self.assertEqual(response.status_code, 200)
        return set(_catalog_ids(response))

    def test_offer_wholesale_returns_only_active_tiers(self):
        self._login_seller()
        ids = self._ids('wholesale')
        self.assertEqual(ids, {self.wholesale.pk})

    def test_offer_sale_returns_only_active_sale(self):
        self._login_seller()
        ids = self._ids('sale')
        self.assertEqual(ids, {self.sale.pk})
        self.assertNotIn(self.expired_sale.pk, ids)

    def test_offer_promo_returns_only_active_promo(self):
        self._login_seller()
        ids = self._ids('promo')
        self.assertEqual(ids, {self.promo.pk})

    def test_offer_consignment_returns_only_enabled(self):
        self._login_seller()
        ids = self._ids('consignment')
        self.assertEqual(ids, {self.consignment.pk})

    def test_product_with_several_tiers_is_not_duplicated(self):
        self._login_seller()
        response = self.client.get(
            self.list_url,
            {'all': '1', 'offer': 'wholesale'},
        )
        ids = _catalog_ids(response)
        self.assertEqual(ids.count(self.wholesale.pk), 1)
        self.assertContains(response, 'ОПТ')
        self.assertEqual(len(ids), 1)

    def test_anonymous_manual_offer_query_does_not_filter(self):
        response = self.client.get(
            self.list_url,
            {'all': '1', 'offer': 'wholesale'},
        )
        self.assertEqual(response.status_code, 200)
        ids = set(_catalog_ids(response))
        self.assertIn(self.wholesale.pk, ids)
        self.assertIn(self.plain.pk, ids)
        self.assertIn(self.sale.pk, ids)
        self.assertNotContains(response, 'Предложения для продавцов')
        self.assertNotContains(response, 'ОПТ')
        self.assertNotContains(response, 'Условия для продавцов')

    def test_plain_user_manual_offer_query_does_not_filter(self):
        User.objects.create_user(username='plain-filter', password='secret12345')
        self.client.login(username='plain-filter', password='secret12345')
        response = self.client.get(
            self.list_url,
            {'all': '1', 'offer': 'sale'},
        )
        ids = set(_catalog_ids(response))
        self.assertIn(self.plain.pk, ids)
        self.assertIn(self.sale.pk, ids)
        self.assertNotContains(response, 'Предложения для продавцов')

    def test_seller_sees_offer_filters_on_catalog(self):
        self._login_seller()
        response = self.client.get(self.list_url, {'all': '1'})
        self.assertContains(response, 'Предложения для продавцов')
        self.assertContains(response, 'Опт')
        self.assertContains(response, 'Распродажа')
        self.assertContains(response, 'Акции')
        self.assertContains(response, 'На реализацию')
        self.assertContains(response, 'Все')

    def test_existing_catalog_list_still_works(self):
        response = self.client.get(self.list_url, {'all': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.plain.title)
        self.assertContains(response, 'Купить')
        self.assertNotContains(response, 'Предложения для продавцов')


class FitmentFilterTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Япония B2B')
        self.toyota = Brand.objects.create(country=country, name='ToyotaB2B')
        self.honda = Brand.objects.create(country=country, name='HondaB2B')
        self.camry = CarModel.objects.create(brand=self.toyota, name='CamryB2B')
        self.corolla = CarModel.objects.create(brand=self.toyota, name='CorollaB2B')
        self.civic = CarModel.objects.create(brand=self.honda, name='CivicB2B')

        self.primary = _make_product(
            title='По основной модели UNIQUE-PRIM',
            slug='fitment-primary',
            article='FIT-PRIM',
            brand=self.toyota,
            car_model=self.camry,
        )
        self.extra_model = _make_product(
            title='По selected_models UNIQUE-SELMOD',
            slug='fitment-selected-model',
            article='FIT-SELMOD',
            brand=self.honda,
            car_model=self.civic,
        )
        self.extra_model.selected_models.add(self.corolla)

        self.extra_brand = _make_product(
            title='По дополнительной марке UNIQUE-SELBR',
            slug='fitment-selected-brand',
            article='FIT-SELBR',
            brand=self.honda,
            car_model=self.civic,
        )
        self.extra_brand.selected_brands.add(self.toyota)

        self.multi_match = _make_product(
            title='Несколько совпадений UNIQUE-MULTI',
            slug='fitment-multi',
            article='FIT-MULTI',
            brand=self.toyota,
            car_model=self.camry,
        )
        self.multi_match.selected_brands.add(self.toyota)
        self.multi_match.selected_models.add(self.camry, self.corolla)

        self.other = _make_product(
            title='Другая марка UNIQUE-OTHER',
            slug='fitment-other',
            article='FIT-OTHER',
            brand=self.honda,
            car_model=self.civic,
        )
        self.list_url = reverse('catalog_list')

    def test_found_by_primary_model(self):
        response = self.client.get(
            self.list_url,
            {'all': '1', 'model': str(self.camry.pk)},
        )
        ids = set(_catalog_ids(response))
        self.assertIn(self.primary.pk, ids)
        self.assertIn(self.multi_match.pk, ids)
        self.assertNotIn(self.other.pk, ids)

    def test_found_by_selected_models(self):
        response = self.client.get(
            self.list_url,
            {'all': '1', 'model': str(self.corolla.pk)},
        )
        ids = set(_catalog_ids(response))
        self.assertIn(self.extra_model.pk, ids)
        self.assertIn(self.multi_match.pk, ids)
        self.assertNotIn(self.primary.pk, ids)
        self.assertNotIn(self.other.pk, ids)

    def test_found_by_additional_brand(self):
        response = self.client.get(
            self.list_url,
            {'all': '1', 'brand': str(self.toyota.pk)},
        )
        ids = set(_catalog_ids(response))
        self.assertIn(self.primary.pk, ids)
        self.assertIn(self.extra_brand.pk, ids)
        self.assertIn(self.extra_model.pk, ids)
        self.assertNotIn(self.other.pk, ids)

    def test_multiple_matches_return_product_once(self):
        response = self.client.get(
            self.list_url,
            {
                'all': '1',
                'brand': str(self.toyota.pk),
                'model': str(self.camry.pk),
            },
        )
        ids = _catalog_ids(response)
        self.assertEqual(ids.count(self.multi_match.pk), 1)

    def test_detail_shows_extra_fitment_without_replacing_compatibility(self):
        response = self.client.get(
            reverse('product_detail', kwargs={'slug': self.multi_match.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CamryB2B')
        self.assertContains(response, 'Также подходит')
        self.assertContains(response, 'CorollaB2B')
        self.assertContains(response, 'Подходит для Camry 2018-2022')


class CartRegressionTests(TestCase):
    def test_cart_add_still_uses_retail_price(self):
        product = _make_product(
            title='Корзина розница UNIQUE-CART',
            slug='b2b-cart-retail',
            article='B2B-CART-1',
            price=RETAIL_PRICE,
            cost_price=COST_PRICE_MARKER,
        )
        ProductPriceTier.objects.create(
            product=product,
            min_qty=10,
            price=900,
        )
        ProductPromotion.objects.create(
            product=product,
            promotion_type=ProductPromotion.TYPE_SALE,
            price=700,
            is_active=True,
        )
        response = self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps({
                'product_id': product.id,
                'quantity': 2,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['cart_total'], RETAIL_PRICE * 2)

        cart_page = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart_page.status_code, 200)
        self.assertContains(cart_page, product.title)
        self.assertNotContains(cart_page, str(COST_PRICE_MARKER))

        cart = CartManager(cart_page.wsgi_request)
        items = cart.get_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['product'].price, RETAIL_PRICE)
        self.assertEqual(items[0]['line_total'], RETAIL_PRICE * 2)
        self.assertEqual(cart.get_total(), RETAIL_PRICE * 2)
