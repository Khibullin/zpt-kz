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

    def test_homepage_has_one_kits_tile_below_search(self):
        home = self.client.get(reverse('catalog_list'))
        html = home.content.decode()
        self.assertContains(home, 'class="tiles"')
        self.assertContains(home, 'site-product-search')
        target = f'href="{reverse("consumables_landing")}"'
        self.assertEqual(html.count(target), 1)
        self.assertGreater(html.find(target), html.find('</header>'))
        self.assertContains(self.client.get(reverse('consumables_landing')),
                            reverse('maintenance_kit_list'))

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
