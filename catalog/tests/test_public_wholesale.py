from django.contrib.auth.models import AnonymousUser, User
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalog.commercial import get_request_seller_profile, resolve_commercial_price
from catalog.models import Brand, Country, Product, ProductPriceTier, SellerProfile
from catalog.wholesale import (
    WHOLESALE_TYPE_CABIN,
    WHOLESALE_TYPE_OIL,
    WHOLESALE_TYPE_SPARK,
    public_wholesale_unit_price,
    wholesale_product_type,
)


RETAIL = 2500
WHOLESALE = 950


def _make_seller(username, name, phone, **kwargs):
    user = User.objects.create_user(username=username, password='secret12345')
    defaults = {
        'user': user,
        'name': name,
        'phone': phone,
        'city': 'Алматы',
        'address': 'г. Алматы, ул. Тестовая, 1',
        'wholesale_enabled': True,
        'wholesale_min_order_qty': 10,
    }
    defaults.update(kwargs)
    return SellerProfile.objects.create(**defaults)


class PublicWholesaleStorefrontTests(TestCase):
    def setUp(self):
        self.seller = _make_seller(
            'wholesale-owner',
            'AG Parts',
            '77771360740',
        )
        self.country = Country.objects.create(name='Китай WH')
        self.haval = Brand.objects.create(country=self.country, name='Haval')
        self.chery = Brand.objects.create(country=self.country, name='Chery')
        self.product = self._product(
            title='Салонный фильтр Haval Jolion',
            article='WH-CABIN-1',
            slug='wh-cabin-1',
        )
        self.product.selected_brands.add(self.haval)
        ProductPriceTier.objects.create(
            product=self.product,
            min_qty=1,
            price=WHOLESALE,
        )

    def _product(self, **kwargs):
        defaults = {
            'title': 'Оптовый товар',
            'price': RETAIL,
            'seller_name': self.seller.name,
            'seller_profile': self.seller,
            'whatsapp_number': '+77771360740',
            'status': 'active',
            'publish_to_sellers': True,
            'city': 'Алматы',
            'article': 'WH-1',
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def _url(self, seller=None):
        seller = seller or self.seller
        return reverse('public_seller_wholesale', kwargs={'slug': seller.slug})

    def test_seller_wholesale_defaults(self):
        other = _make_seller(
            'plain-owner',
            'Plain Shop',
            '77770000099',
            wholesale_enabled=False,
        )
        self.assertFalse(other.wholesale_enabled)
        self.assertEqual(other.wholesale_min_order_qty, 10)

    def test_anonymous_sees_public_wholesale_price(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertIn('Опт:', html)
        self.assertIn(str(WHOLESALE), html)
        self.assertIn('₸/шт', html)
        self.assertIn(self.product.title, html)
        self.assertIn(self.product.article, html)
        self.assertIn('Купить оптом', html)
        self.assertIn(
            'AG Parts — оптовые поставки автокомпонентов из Китая',
            html,
        )
        self.assertIn(
            'Постоянные прямые оптовые цены для магазинов',
            html,
        )
        self.assertIn(
            'Минимальный оптовый заказ — 10 единиц в ассортименте',
            html,
        )
        lowered = html.lower()
        for banned in ('скидка', 'распродажа', 'акция'):
            self.assertNotIn(banned, lowered)

    def test_anonymous_retail_catalog_price_unchanged(self):
        factory = RequestFactory()
        request = factory.get('/market/')
        request.user = AnonymousUser()
        quote = resolve_commercial_price(
            self.product,
            10,
            seller_profile=get_request_seller_profile(request),
        )
        self.assertEqual(quote.unit_price, RETAIL)
        self.assertEqual(public_wholesale_unit_price(self.product), WHOLESALE)

    def test_publish_to_sellers_false_excluded(self):
        hidden_flag = self._product(
            title='Не для продавцов фильтр',
            article='WH-NOPUB',
            slug='wh-nopub',
            publish_to_sellers=False,
        )
        ProductPriceTier.objects.create(product=hidden_flag, min_qty=1, price=700)
        html = self.client.get(self._url()).content.decode('utf-8')
        self.assertNotIn(hidden_flag.title, html)
        self.assertNotIn(hidden_flag.article, html)

    def test_hidden_product_excluded(self):
        hidden = self._product(
            title='Скрытый салонный фильтр',
            article='WH-HIDDEN',
            slug='wh-hidden',
            status='hidden',
        )
        ProductPriceTier.objects.create(product=hidden, min_qty=1, price=700)
        html = self.client.get(self._url()).content.decode('utf-8')
        self.assertNotIn(hidden.title, html)

    def test_product_without_active_tier_excluded(self):
        no_tier = self._product(
            title='Без оптовой цены фильтр',
            article='WH-NOTIER',
            slug='wh-notier',
        )
        inactive = self._product(
            title='Неактивный тариф фильтр',
            article='WH-INACTIVE',
            slug='wh-inactive',
        )
        ProductPriceTier.objects.create(
            product=inactive,
            min_qty=1,
            price=700,
            is_active=False,
        )
        html = self.client.get(self._url()).content.decode('utf-8')
        self.assertNotIn(no_tier.title, html)
        self.assertNotIn(inactive.title, html)

    def test_wholesale_disabled_storefront_is_404(self):
        self.seller.wholesale_enabled = False
        self.seller.save(update_fields=['wholesale_enabled'])
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_public_profile_shows_wholesale_cta(self):
        url = reverse('public_seller_profile', kwargs={'slug': self.seller.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertIn('Оптовые цены', html)
        self.assertIn(self._url(), html)

    def test_public_profile_hides_cta_when_disabled(self):
        self.seller.wholesale_enabled = False
        self.seller.save(update_fields=['wholesale_enabled'])
        url = reverse('public_seller_profile', kwargs={'slug': self.seller.slug})
        html = self.client.get(url).content.decode('utf-8')
        self.assertNotIn('Оптовые цены', html)

    def test_type_and_brand_filters(self):
        oil = self._product(
            title='Масляный фильтр Chery Tiggo',
            article='WH-OIL-1',
            slug='wh-oil-1',
        )
        oil.selected_brands.add(self.chery)
        ProductPriceTier.objects.create(product=oil, min_qty=1, price=880)
        spark = self._product(
            title='Свеча зажигания Haval',
            article='WH-SPARK-1',
            slug='wh-spark-1',
        )
        spark.selected_brands.add(self.haval)
        ProductPriceTier.objects.create(product=spark, min_qty=1, price=420)

        self.assertEqual(wholesale_product_type(self.product), WHOLESALE_TYPE_CABIN)
        self.assertEqual(wholesale_product_type(oil), WHOLESALE_TYPE_OIL)
        self.assertEqual(wholesale_product_type(spark), WHOLESALE_TYPE_SPARK)

        cabin_html = self.client.get(
            self._url(),
            {'type': WHOLESALE_TYPE_CABIN},
        ).content.decode('utf-8')
        self.assertIn(self.product.title, cabin_html)
        self.assertNotIn(oil.title, cabin_html)

        brand_html = self.client.get(
            self._url(),
            {'brand': self.chery.id},
        ).content.decode('utf-8')
        self.assertIn(oil.title, brand_html)
        self.assertNotIn(self.product.title, brand_html)

        search_html = self.client.get(
            self._url(),
            {'q': 'Jolion'},
        ).content.decode('utf-8')
        self.assertIn(self.product.title, search_html)
        self.assertNotIn(oil.title, search_html)

    def _detail_url(self, product=None):
        product = product or self.product
        return reverse('product_detail', kwargs={'slug': product.slug})

    def test_anonymous_product_detail_shows_wholesale_block(self):
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8').replace('\xa0', ' ')
        self.assertIn('Розничная цена:', html)
        self.assertIn('2 500', html)
        self.assertIn('Оптовая цена:', html)
        self.assertIn(str(WHOLESALE), html)
        self.assertIn('Минимальный оптовый заказ — 10 единиц', html)
        self.assertIn('Купить оптом', html)
        self.assertIn('Купить в розницу', html)
        self.assertIn('Смотреть весь оптовый ассортимент продавца', html)
        self.assertIn(self._url(), html)
        self.assertIn('Наличие подтверждается при оформлении заказа.', html)
        lowered = html.lower()
        for banned in ('скидка', 'распродажа', 'акция'):
            self.assertNotIn(banned, lowered)

    def test_ineligible_product_detail_has_no_wholesale_block(self):
        ineligible = self._product(
            title='Обычный розничный товар',
            article='WH-RETAIL-ONLY',
            slug='wh-retail-only',
            publish_to_sellers=False,
        )
        response = self.client.get(self._detail_url(ineligible))
        html = response.content.decode('utf-8')
        self.assertNotIn('Оптовая цена:', html)
        self.assertNotIn('Купить оптом', html)
        self.assertNotIn('Есть оптовая цена', html)

    def test_catalog_shows_wholesale_badge_without_price(self):
        catalog = self.client.get(reverse('catalog_list'), {'q': self.product.article})
        html = catalog.content.decode('utf-8').replace('\xa0', ' ')
        self.assertIn('Есть оптовая цена', html)
        self.assertNotIn(f'{WHOLESALE} ₸/шт', html)
        self.assertIn('2 500', html)

    def test_catalog_hides_badge_when_not_eligible(self):
        no_publish = self._product(
            title='Без публикации продавцам',
            article='WH-NOBADGE-1',
            slug='wh-nobadge-1',
            publish_to_sellers=False,
        )
        ProductPriceTier.objects.create(product=no_publish, min_qty=1, price=400)
        no_tier = self._product(
            title='Без активного тарифа',
            article='WH-NOBADGE-2',
            slug='wh-nobadge-2',
        )
        disabled_seller = _make_seller(
            'disabled-wh',
            'No Wholesale Shop',
            '77770000088',
            wholesale_enabled=False,
        )
        disabled_product = Product.objects.create(
            title='Товар выключенного опта',
            price=RETAIL,
            seller_name=disabled_seller.name,
            seller_profile=disabled_seller,
            whatsapp_number='+77770000088',
            status='active',
            publish_to_sellers=True,
            city='Алматы',
            article='WH-NOBADGE-3',
            slug='wh-nobadge-3',
        )
        ProductPriceTier.objects.create(product=disabled_product, min_qty=1, price=400)

        for article, title in (
            (no_publish.article, no_publish.title),
            (no_tier.article, no_tier.title),
            (disabled_product.article, disabled_product.title),
        ):
            html = self.client.get(
                reverse('catalog_list'),
                {'q': article},
            ).content.decode('utf-8')
            self.assertIn(title, html)
            self.assertNotIn('Есть оптовая цена', html)

    def test_catalog_wholesale_badge_query_count_stable(self):
        for index in range(4):
            extra = self._product(
                title=f'Салонный фильтр extra {index}',
                article=f'WH-N1-{index}',
                slug=f'wh-n1-{index}',
            )
            ProductPriceTier.objects.create(product=extra, min_qty=1, price=800)
        url = reverse('catalog_list')
        params = {'q': 'Салонный фильтр'}
        with CaptureQueriesContext(connection) as first:
            self.client.get(url, params)
        baseline = len(first.captured_queries)
        for index in range(4, 8):
            extra = self._product(
                title=f'Салонный фильтр extra {index}',
                article=f'WH-N1-{index}',
                slug=f'wh-n1-{index}',
            )
            ProductPriceTier.objects.create(product=extra, min_qty=1, price=800)
        with CaptureQueriesContext(connection) as second:
            response = self.client.get(url, params)
        self.assertLessEqual(len(second.captured_queries), baseline + 1)
        self.assertContains(response, 'Есть оптовая цена')

    def test_product_detail_captures_utm_without_empty_overwrite(self):
        from orders.constants import SESSION_UTM_KEY

        self.client.get(
            self._detail_url(),
            {
                'utm_source': 'whatsapp',
                'utm_medium': 'marketing',
                'utm_campaign': 'launch',
            },
        )
        self.client.get(self._detail_url())
        stored = self.client.session[SESSION_UTM_KEY]
        self.assertEqual(stored['utm_source'], 'whatsapp')
        self.assertEqual(stored['utm_campaign'], 'launch')

