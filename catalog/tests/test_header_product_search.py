from django.test import TestCase
from django.urls import reverse

from catalog.models import Brand, Category, Country, Product, ProductPriceTier


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'HDR-TEST',
        'stock_qty': 4,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    product = Product.objects.create(**defaults)
    ProductPriceTier.objects.create(product=product, min_qty=1, price=product.price)
    return product


class HeaderProductSearchTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай HDR')
        self.chery = Brand.objects.create(country=country, name='Chery')
        self.category = Category.objects.create(name='Фильтры HDR')
        self.air = _product(
            article='151000079AA',
            title='Воздушный фильтр Chery Tiggo 8 Pro 1.6 / Arrizo 8 — 151000079AA',
            slug='vozdushnyi-filtr-151000079aa',
            brand=self.chery,
            category=self.category,
        )
        self.m111 = _product(
            article='M111109111',
            title='Воздушный фильтр Chery A3 / M11 — M111109111',
            slug='m111109111',
            brand=self.chery,
            category=self.category,
        )
        self.cabin = _product(
            article='T218107011',
            title='Салонный фильтр Chery Tiggo 7 / Tiggo 8 Pro — T218107011',
            slug='chery-tiggo-7-t218107011-chery-tiggo-7',
            brand=self.chery,
            category=self.category,
        )

    def _assert_header_search(self, response):
        self.assertContains(response, 'class="site-product-search"')
        self.assertContains(response, 'placeholder="Артикул или название запчасти"')
        self.assertContains(response, '>Найти</button>')
        self.assertContains(response, f'action="{reverse("catalog_list")}"')
        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'required')
        self.assertContains(response, 'header-search.css')

    def test_header_search_is_on_home_kits_and_product(self):
        home = self.client.get(reverse('catalog_list'))
        self._assert_header_search(home)

        kits = self.client.get(reverse('maintenance_kit_list'))
        self._assert_header_search(kits)

        product = self.client.get(self.m111.get_absolute_url())
        self.assertEqual(product.status_code, 200)
        self._assert_header_search(product)

        cart = self.client.get(reverse('orders:cart'))
        self._assert_header_search(cart)

    def test_article_and_title_search_use_existing_results_page(self):
        air = self.client.get(reverse('catalog_list'), {'q': '151000079AA'})
        self.assertContains(air, 'id="catalog-results"')
        self.assertNotContains(air, 'class="products home-showcase-products"')
        self.assertContains(air, 'value="151000079AA"')
        self.assertContains(air, '/vozdushnyi-filtr-151000079aa/')
        self.assertContains(air, self.air.title)
        self.assertNotContains(air, self.m111.title)

        m111 = self.client.get(reverse('catalog_list'), {'q': 'M111109111'})
        self.assertContains(m111, 'value="M111109111"')
        self.assertContains(m111, '/m111109111/')
        self.assertContains(m111, self.m111.title)
        self.assertNotContains(m111, self.air.title)

        cabin = self.client.get(reverse('catalog_list'), {'q': 'Салонный фильтр'})
        self.assertContains(cabin, self.cabin.title)
        self.assertContains(cabin, self.cabin.get_absolute_url())
        self.assertNotContains(cabin, self.air.title)

    def test_empty_query_does_not_open_listing_results(self):
        empty = self.client.get(reverse('catalog_list'), {'q': ''})
        self.assertContains(empty, 'id="home-parts-form"')
        self.assertContains(empty, 'class="products home-showcase-products"')

        spaces = self.client.get(reverse('catalog_list'), {'q': '   '})
        self.assertContains(spaces, 'id="home-parts-form"')
        self.assertContains(spaces, 'class="products home-showcase-products"')
