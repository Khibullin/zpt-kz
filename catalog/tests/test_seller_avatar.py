import re

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import Product, SellerProfile

MINIMAL_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
    b'\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\x0d\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82'
)

ZPT_LOGO_MARKERS = (
    'images/logo.png',
    'images/logo.jpg',
)

INITIALS_MARKERS = (
    'seller-avatar--initials',
    'product-seller-avatar--initials',
    'seller_initials',
    '>GG<',
    '>AP<',
)


def seller_logo_blocks(html):
    return re.findall(
        r'<img\b[^>]*\bseller-logo\b[^>]*>',
        html,
        flags=re.I,
    )


class SellerLogoDisplayTests(TestCase):
    def setUp(self):
        self.client = Client()

    @staticmethod
    def _create_seller(name, phone, *, with_logo=False):
        user = User.objects.create_user(
            username=phone,
            password='testpass123',
        )
        logo = None
        if with_logo:
            logo = SimpleUploadedFile(
                f'{phone}-logo.png',
                MINIMAL_PNG,
                content_type='image/png',
            )
        seller = SellerProfile(
            user=user,
            name=name,
            phone=phone,
            city='Алматы',
            logo=logo,
        )
        seller.save()
        return seller

    @staticmethod
    def _create_product(seller, slug_suffix):
        return Product.objects.create(
            title=f'Product {slug_suffix}',
            slug=f'product-{slug_suffix}',
            price=15000,
            seller_name=seller.name,
            whatsapp_number=seller.phone,
            status='active',
            article=f'ART-{slug_suffix}',
            city='Алматы',
        )

    def _assert_no_initials(self, html):
        for marker in INITIALS_MARKERS:
            self.assertNotIn(
                marker,
                html,
                msg=f'Unexpected initials marker {marker!r}',
            )

    def _assert_trust_card_has_no_zpt_logo(self, html):
        match = re.search(
            r'<div class="seller-trust-card">.*?</div>\s*</div>\s*</div>',
            html,
            flags=re.S,
        )
        if not match:
            return
        trust_card = match.group(0)
        for marker in ZPT_LOGO_MARKERS:
            self.assertNotIn(
                marker,
                trust_card,
                msg=f'ZPT logo marker {marker!r} found in seller trust card',
            )

    def _assert_no_empty_avatar_containers(self, html):
        self.assertNotRegex(
            html,
            r'product-seller-avatar',
            msg='Empty product seller avatar container found',
        )
        self.assertNotRegex(
            html,
            r'seller-avatar--initials',
            msg='Seller initials avatar container found',
        )

    def test_seller_with_logo_shows_logo_on_product_page(self):
        seller = self._create_seller('grm4x4 Shop', '77001110001', with_logo=True)
        product = self._create_product(seller, 'logo-case')

        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('seller-logo', html)
        self.assertIn(seller.logo.url, html)
        self._assert_no_initials(html)

    def test_seller_without_logo_shows_no_logo_block_on_product_page(self):
        seller = self._create_seller('Gigant Group', '77001110002')
        product = self._create_product(seller, 'no-logo-case')

        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Gigant Group', html)
        self.assertNotIn('seller-logo', html)
        self._assert_no_initials(html)
        self._assert_trust_card_has_no_zpt_logo(html)
        self._assert_no_empty_avatar_containers(html)

    def test_public_profile_without_logo_returns_200_without_logo(self):
        seller = self._create_seller('Gigant Group', '77001110003')

        response = self.client.get(
            reverse('public_seller_profile', kwargs={'slug': seller.slug})
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Gigant Group', html)
        self.assertNotIn('seller-logo', html)
        self._assert_no_initials(html)
        self._assert_trust_card_has_no_zpt_logo(html)
        self._assert_no_empty_avatar_containers(html)

    def test_catalog_list_without_logo_shows_name_without_logo(self):
        seller = self._create_seller('AG Parts', '77001110004')
        self._create_product(seller, 'catalog-case')

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('AG Parts', html)
        logo_blocks = seller_logo_blocks(html)
        self.assertFalse(
            any('AG Parts' in block for block in logo_blocks),
            msg='Unexpected seller logo img for seller without logo',
        )
        self._assert_no_initials(html)
        self._assert_no_empty_avatar_containers(html)

    def test_seller_with_logo_still_renders_on_public_profile_and_product_page(self):
        seller = self._create_seller('Logo Seller', '77001110005', with_logo=True)
        product = self._create_product(seller, 'public-logo-case')

        profile_response = self.client.get(
            reverse('public_seller_profile', kwargs={'slug': seller.slug})
        )
        product_response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )

        self.assertEqual(profile_response.status_code, 200)
        self.assertContains(profile_response, 'seller-logo')
        self.assertContains(profile_response, seller.logo.url)

        self.assertEqual(product_response.status_code, 200)
        self.assertContains(product_response, 'seller-logo')
        self.assertContains(product_response, seller.logo.url)

        self._assert_no_initials(profile_response.content.decode())
        self._assert_no_initials(product_response.content.decode())
