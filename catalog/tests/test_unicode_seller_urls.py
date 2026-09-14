from urllib.parse import unquote

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, ProductPriceTier, SellerProfile

UNICODE_SLUG = 'үрімші-авто'
ASCII_SLUG = 'ag-parts'


def _make_seller(*, username, name, phone, slug, wholesale_enabled=False):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
        city='Алматы',
        slug=slug,
        wholesale_enabled=wholesale_enabled,
    )


def _make_product(seller, *, title, article, slug):
    return Product.objects.create(
        title=title,
        slug=slug,
        article=article,
        price=1500,
        seller_name=seller.name,
        seller_profile=seller,
        whatsapp_number=seller.phone,
        status='active',
        city='Алматы',
        publish_to_sellers=True,
    )


class UnicodeSellerUrlTests(TestCase):
    def setUp(self):
        self.seller = _make_seller(
            username='urimshi-owner',
            name='Үрімші Авто',
            phone='77001110001',
            slug=UNICODE_SLUG,
            wholesale_enabled=True,
        )
        self.product = _make_product(
            self.seller,
            title='Фильтр Үрімші',
            article='URIM-1',
            slug='urimshi-filter-1',
        )
        ProductPriceTier.objects.create(
            product=self.product,
            min_qty=1,
            price=950,
        )

    def test_unicode_profile_reverse_and_get(self):
        url = reverse('public_seller_profile', args=[self.seller.slug])
        self.assertIn(UNICODE_SLUG, unquote(url))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.seller.name)

    def test_unicode_wholesale_reverse_and_get(self):
        wholesale_url = reverse(
            'public_seller_wholesale',
            args=[self.seller.slug],
        )
        price_url = reverse(
            'public_seller_wholesale_price',
            args=[self.seller.slug],
        )
        self.assertIn(UNICODE_SLUG, unquote(wholesale_url))
        self.assertIn(UNICODE_SLUG, unquote(price_url))

        wholesale = self.client.get(wholesale_url)
        self.assertEqual(wholesale.status_code, 200)

        price = self.client.get(price_url)
        self.assertEqual(price.status_code, 200)

    def test_catalog_home_and_all_survive_unicode_seller_card(self):
        home = self.client.get('/')
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, self.product.title)

        catalog_all = self.client.get('/?all=1')
        self.assertEqual(catalog_all.status_code, 200)
        self.assertContains(catalog_all, self.product.title)


class AsciiSellerUrlRegressionTests(TestCase):
    def setUp(self):
        self.seller = _make_seller(
            username='ag-parts-owner',
            name='AG Parts',
            phone='77001110002',
            slug=ASCII_SLUG,
            wholesale_enabled=True,
        )
        product = _make_product(
            self.seller,
            title='Фильтр AG Parts',
            article='AG-1',
            slug='ag-parts-filter-1',
        )
        ProductPriceTier.objects.create(
            product=product,
            min_qty=1,
            price=950,
        )

    def test_ascii_seller_urls_still_resolve(self):
        profile = reverse('public_seller_profile', args=[self.seller.slug])
        wholesale = reverse('public_seller_wholesale', args=[self.seller.slug])
        price = reverse('public_seller_wholesale_price', args=[self.seller.slug])
        self.assertEqual(profile, f'/seller/{ASCII_SLUG}/')
        self.assertEqual(wholesale, f'/seller/{ASCII_SLUG}/wholesale/')
        self.assertEqual(price, f'/seller/{ASCII_SLUG}/wholesale/price.xlsx')

        self.assertEqual(self.client.get(profile).status_code, 200)
        self.assertEqual(self.client.get(wholesale).status_code, 200)
        self.assertEqual(self.client.get(price).status_code, 200)
