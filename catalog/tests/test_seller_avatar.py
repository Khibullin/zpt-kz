import re

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import Product, SellerProfile
from catalog.seller_initials import seller_initials

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


def seller_avatar_blocks(html):
    blocks = re.findall(
        r'<div\b[^>]*\bseller-avatar--initials\b[^>]*>.*?</div>',
        html,
        flags=re.S,
    )
    blocks.extend(re.findall(
        r'<(?:a|div)\b[^>]*\bseller-card-logo\b[^>]*>.*?</(?:a|div)>',
        html,
        flags=re.S,
    ))
    return blocks


def assert_seller_blocks_have_no_zpt_logo(test_case, response):
    blocks = seller_avatar_blocks(response.content.decode())
    test_case.assertTrue(blocks, 'Expected at least one seller avatar block')
    for block in blocks:
        for marker in ZPT_LOGO_MARKERS:
            test_case.assertNotIn(
                marker,
                block,
                msg=f'ZPT logo marker {marker!r} found in seller avatar block',
            )


class SellerInitialsHelperTests(TestCase):
    def test_gigant_group_initials(self):
        self.assertEqual(seller_initials('Gigant Group'), 'GG')

    def test_single_word_initials(self):
        self.assertEqual(seller_initials('grm4x4'), 'G')

    def test_cyrillic_initials(self):
        self.assertEqual(seller_initials('Авто Мир'), 'АМ')

    def test_skips_legal_prefix(self):
        self.assertEqual(seller_initials('ТОО Gigant Group Kazakhstan'), 'GG')

    def test_empty_name_returns_neutral_initial(self):
        self.assertEqual(seller_initials(''), 'M')
        self.assertEqual(seller_initials('   '), 'M')
        self.assertEqual(seller_initials(None), 'M')


class SellerAvatarDisplayTests(TestCase):
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

    def test_seller_with_logo_shows_logo_on_product_page(self):
        seller = self._create_seller('grm4x4 Shop', '77001110001', with_logo=True)
        product = self._create_product(seller, 'logo-case')

        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'seller-avatar--logo')
        self.assertContains(response, seller.logo.url)
        self.assertNotContains(response, 'seller-avatar--initials')

    def test_seller_without_logo_shows_initials_not_zpt_logo(self):
        seller = self._create_seller('Gigant Group', '77001110002')
        product = self._create_product(seller, 'no-logo-case')

        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'seller-avatar--initials')
        self.assertContains(response, '>GG<')
        self.assertContains(response, 'aria-label="Gigant Group"')
        assert_seller_blocks_have_no_zpt_logo(self, response)

    def test_public_profile_without_logo_returns_200_with_initials(self):
        seller = self._create_seller('Gigant Group', '77001110003')

        response = self.client.get(
            reverse('public_seller_profile', kwargs={'slug': seller.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'seller-avatar--initials')
        self.assertContains(response, '>GG<')
        assert_seller_blocks_have_no_zpt_logo(self, response)

    def test_catalog_list_without_logo_uses_initials_not_zpt_logo(self):
        seller = self._create_seller('AG Parts', '77001110004')
        self._create_product(seller, 'catalog-case')

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'seller-avatar--initials')
        self.assertContains(response, '>AP<')
        assert_seller_blocks_have_no_zpt_logo(self, response)

    def test_seller_with_logo_still_renders_on_public_profile(self):
        seller = self._create_seller('Logo Seller', '77001110005', with_logo=True)
        product = self._create_product(seller, 'public-logo-case')

        profile_response = self.client.get(
            reverse('public_seller_profile', kwargs={'slug': seller.slug})
        )
        product_response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )

        self.assertEqual(profile_response.status_code, 200)
        self.assertContains(profile_response, 'seller-avatar--logo')
        self.assertContains(profile_response, seller.logo.url)

        self.assertEqual(product_response.status_code, 200)
        self.assertContains(product_response, 'seller-avatar--logo')
        self.assertContains(product_response, seller.logo.url)
