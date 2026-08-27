from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase
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
