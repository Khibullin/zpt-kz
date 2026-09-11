import json
import re
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, SellerProfile
from catalog.templatetags.product_extras import public_product_url
from orders.tests.test_manual_checkout import create_product


SHARE_PATH_RE = re.compile(r'data-share-path="([^"]*)"')
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SLUG = 'toyota-3s-fe-std-toyota-rav4'
LEGACY_SLUG = 'audi'
REMAPPED_SLUG = 'peugeot-308-hu71151x'


def share_paths(html):
    return SHARE_PATH_RE.findall(html)


class ProductShareLinksTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='share-seller',
            password='secret12345',
        )
        self.seller = SellerProfile.objects.create(
            user=self.user,
            name='Share Seller',
            phone='77001112233',
            city='Алматы',
            slug='share-seller',
        )

    def _product(self, *, title, slug, article, seller_profile=None, seller_name=''):
        return Product.objects.create(
            title=title,
            slug=slug,
            article=article,
            price=1950,
            seller_name=seller_name or self.seller.name,
            seller_profile=seller_profile if seller_profile is not None else self.seller,
            whatsapp_number='77700000000',
            status='active',
            city='Алматы',
        )

    def _assert_no_search_share_urls(self, html):
        for path in share_paths(html):
            self.assertTrue(path.startswith('/'), path)
            self.assertNotIn('?', path)
            self.assertNotIn('q=', path)
            self.assertFalse(path.startswith('/?'))
        self.assertNotIn('data-share-path="/?', html)
        self.assertNotIn('data-share-path="?', html)

    def test_search_card_share_uses_canonical_detail_url(self):
        product = self._product(
            title='Поршень двигателя Toyota 3S-FE STD',
            slug=CANONICAL_SLUG,
            article='SHARE-SEARCH-1',
        )
        canonical_path = public_product_url(product)
        response = self.client.get(
            reverse('catalog_list'),
            {'q': 'Поршень двигателя Toyota 3S-FE STD'},
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(canonical_path, f'/{CANONICAL_SLUG}/')
        self.assertIn(canonical_path, share_paths(html))
        self._assert_no_search_share_urls(html)
        self.assertContains(response, 'data-product-share')
        self.assertContains(response, 'aria-label="Поделиться товаром"')
        self.assertContains(response, 'js/product-share.js')
        self.assertContains(
            response,
            '<meta name="robots" content="noindex, follow">',
            html=True,
        )
        self.assertContains(
            response,
            '<link rel="canonical" href="https://zpt.kz/">',
            html=True,
        )

    def test_legacy_slug_share_uses_remapped_canonical_url(self):
        product = self._product(
            title='Legacy remap share product',
            slug=LEGACY_SLUG,
            article='SHARE-LEGACY-1',
        )
        response = self.client.get(
            reverse('catalog_list'),
            {'q': product.article},
        )
        html = response.content.decode()
        paths = share_paths(html)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(public_product_url(product), f'/{REMAPPED_SLUG}/')
        self.assertIn(f'/{REMAPPED_SLUG}/', paths)
        self.assertNotIn(f'/{LEGACY_SLUG}/', paths)
        self._assert_no_search_share_urls(html)

    def test_detail_share_url_has_no_query_params(self):
        product = self._product(
            title='Detail share product',
            slug=CANONICAL_SLUG,
            article='SHARE-DETAIL-1',
        )
        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug}),
            {'utm_source': 'share-test', 'q': 'should-not-leak'},
        )
        html = response.content.decode()
        paths = share_paths(html)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(paths, [f'/{CANONICAL_SLUG}/'])
        self.assertNotIn('utm_source', paths[0])
        self.assertNotIn('?', paths[0])
        self._assert_no_search_share_urls(html)
        self.assertContains(response, 'product-share-btn--detail')

    def test_seller_profile_card_uses_canonical_share_url(self):
        product = self._product(
            title='Seller profile share product',
            slug=CANONICAL_SLUG,
            article='SHARE-PROFILE-1',
        )
        response = self.client.get(
            reverse('public_seller_profile', kwargs={'slug': self.seller.slug})
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'/{CANONICAL_SLUG}/', share_paths(html))
        self.assertContains(response, f'href="/{CANONICAL_SLUG}/"')
        self._assert_no_search_share_urls(html)

    def test_related_product_uses_canonical_public_url(self):
        main = self._product(
            title='Main related share product',
            slug='share-main-related',
            article='SHARE-REL-MAIN',
        )
        related = self._product(
            title='Related legacy product',
            slug=LEGACY_SLUG,
            article='SHARE-REL-LEGACY',
        )
        response = self.client.get(
            reverse('product_detail', kwargs={'slug': main.slug})
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(related, response.context['seller_products'])
        self.assertContains(response, 'seller-related-product-link')
        self.assertContains(response, f'href="/{REMAPPED_SLUG}/"')
        self.assertNotContains(response, f'href="/{LEGACY_SLUG}/"')
        self.assertEqual(public_product_url(related), f'/{REMAPPED_SLUG}/')

    def test_seller_dashboard_public_product_link_uses_canonical_url(self):
        product = self._product(
            title='Dashboard public link product',
            slug=LEGACY_SLUG,
            article='SHARE-DASH-1',
        )
        self.client.login(username='share-seller', password='secret12345')
        response = self.client.get(reverse('seller_dashboard'))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, product.title)
        self.assertIn(f'href="/{REMAPPED_SLUG}/"', html)
        self.assertNotIn(f'href="/{LEGACY_SLUG}/"', html)
        self.assertEqual(public_product_url(product), f'/{REMAPPED_SLUG}/')

    def test_cart_product_link_uses_canonical_url(self):
        product = create_product(
            title='Cart share product',
            slug=LEGACY_SLUG,
            article='SHARE-CART-1',
            seller_name=self.seller.name,
            whatsapp_number=self.seller.phone,
        )
        product.seller_profile = self.seller
        product.save(update_fields=['seller_profile'])

        add_response = self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps({'product_id': product.id, 'quantity': 1}),
            content_type='application/json',
        )
        self.assertEqual(add_response.status_code, 200)

        response = self.client.get(reverse('orders:cart'))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'checkout-summary-thumb-link')
        self.assertContains(response, 'checkout-summary-title-link')
        self.assertIn(f'href="/{REMAPPED_SLUG}/"', html)
        self.assertNotIn(f'href="/{LEGACY_SLUG}/"', html)
        self.assertNotIn('data-share-path="/?', html)

    def test_share_script_builds_url_from_canonical_path_not_location_href(self):
        js = (PROJECT_ROOT / 'static' / 'js' / 'product-share.js').read_text(
            encoding='utf-8'
        )

        self.assertIn("button.getAttribute('data-share-path')", js)
        self.assertIn('window.location.origin', js)
        self.assertIn('navigator.share', js)
        self.assertIn('clipboard', js)
        self.assertNotIn('window.location.href', js)
        self.assertNotIn('/?q=', js)
        self.assertIn("path.indexOf('?')", js)
