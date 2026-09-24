from django.test import TestCase
from django.urls import reverse

from catalog.models import Brand, Category, Country, Product, ProductPriceTier


class HomeKitsEntryTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай KITBTN')
        brand = Brand.objects.create(country=country, name='Chery KITBTN')
        category = Category.objects.create(name='Фильтры KITBTN')
        product = Product.objects.create(
            title='Салонный фильтр KITBTN',
            price=1000,
            seller_name='AG Parts',
            whatsapp_number='+77713607040',
            status='active',
            article='KITBTN-T218',
            stock_qty=4,
            city='Алматы',
            brand=brand,
            category=category,
        )
        ProductPriceTier.objects.create(product=product, min_qty=1, price=product.price)
        self.product = product

    def test_homepage_has_single_kits_button_under_search_not_in_header(self):
        home = self.client.get(reverse('catalog_list'))
        self.assertContains(home, 'class="home-kits-entry"')
        self.assertContains(home, 'class="home-kits-entry-btn"')
        self.assertContains(home, 'Комплекты ТО →')
        self.assertContains(home, f'href="{reverse("maintenance_kit_list")}"')
        self.assertContains(home, 'class="site-product-search"')
        self.assertEqual(home.content.decode().count('Комплекты ТО →'), 1)

        html = home.content.decode()
        header_end = html.find('</header>')
        kits_pos = html.find('class="home-kits-entry"')
        search_pos = html.find('class="site-product-search"')
        self.assertGreater(header_end, 0)
        self.assertGreater(kits_pos, header_end)
        self.assertGreater(header_end, search_pos)
        self.assertGreater(kits_pos, search_pos)

    def test_kits_button_stays_off_listing_and_other_header_pages(self):
        listing = self.client.get(reverse('catalog_list'), {'q': 'KITBTN-T218'})
        self.assertContains(listing, 'id="catalog-results"')
        self.assertNotContains(listing, 'class="home-kits-entry"')
        self.assertNotContains(listing, 'Комплекты ТО →')

        product = self.client.get(self.product.get_absolute_url())
        self.assertEqual(product.status_code, 200)
        self.assertNotContains(product, 'class="home-kits-entry"')

        kits = self.client.get(reverse('maintenance_kit_list'))
        self.assertNotContains(kits, 'class="home-kits-entry"')
        self.assertNotContains(kits, 'Комплекты ТО →')

        cart = self.client.get(reverse('orders:cart'))
        self.assertNotContains(cart, 'class="home-kits-entry"')
