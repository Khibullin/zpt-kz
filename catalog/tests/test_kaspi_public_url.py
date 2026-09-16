from django.core.exceptions import ValidationError
from django.forms.models import modelform_factory
from django.test import TestCase

from catalog.kaspi_public_url import display_kaspi_public_url, validate_kaspi_public_url
from catalog.models import Product, ProductKaspiListing, SellerProfile
from django.contrib.auth.models import User


VALID = 'https://kaspi.kz/shop/p/filtr-maslianyi-901-102-123456789/'


def _listing(**kwargs):
    user = User.objects.create_user(username='kaspi-url-seller', password='secret12345')
    seller = SellerProfile.objects.create(
        user=user,
        name='kaspi-url-seller',
        phone='77070000009',
        city='Алматы',
    )
    product = Product.objects.create(
        title='Filter',
        article='URL-ART',
        seller_name=seller.name,
        whatsapp_number=seller.phone,
        seller_profile=seller,
        city=seller.city,
        status='active',
        price=5000,
    )
    defaults = {
        'product': product,
        'master_sku': '111222',
        'merchant_sku': 'URL-ART',
    }
    defaults.update(kwargs)
    return ProductKaspiListing(**defaults)


class KaspiPublicUrlValidationTests(TestCase):
    def test_empty_is_allowed(self):
        validate_kaspi_public_url('')
        validate_kaspi_public_url(None)
        listing = _listing(public_url='')
        listing.full_clean()

    def test_https_shop_product_is_accepted(self):
        validate_kaspi_public_url(VALID)
        listing = _listing(public_url=VALID)
        listing.full_clean()

    def test_www_host_is_accepted(self):
        validate_kaspi_public_url(
            'https://www.kaspi.kz/shop/p/filtr-vozdushnyi-555/'
        )

    def test_http_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_kaspi_public_url('http://kaspi.kz/shop/p/filtr-555/')
        listing = _listing(public_url='http://kaspi.kz/shop/p/filtr-555/')
        with self.assertRaises(ValidationError):
            listing.full_clean()

    def test_external_domain_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_kaspi_public_url('https://example.com/shop/p/filtr-555/')

    def test_api_path_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_kaspi_public_url('https://kaspi.kz/shop/api/products/1')

    def test_non_product_shop_path_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_kaspi_public_url('https://kaspi.kz/shop/c/filters/')

    def test_javascript_is_rejected(self):
        listing = _listing(public_url='javascript:alert(1)')
        with self.assertRaises(ValidationError):
            listing.full_clean()

    def test_model_form_rejects_invalid_url(self):
        listing = _listing()
        listing.save()
        Form = modelform_factory(
            ProductKaspiListing,
            fields=['master_sku', 'public_url'],
        )
        form = Form(
            data={'master_sku': listing.master_sku, 'public_url': 'https://evil.test/shop/p/x/'},
            instance=listing,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('public_url', form.errors)

    def test_display_helper_hides_invalid_values(self):
        self.assertEqual(display_kaspi_public_url(VALID), VALID)
        self.assertEqual(display_kaspi_public_url(''), '')
        self.assertEqual(
            display_kaspi_public_url('https://kaspi.kz/shop/api/products/1'),
            '',
        )

    def test_url_is_not_derived_from_master_sku(self):
        listing = _listing(master_sku='999888777')
        listing.save()
        listing.refresh_from_db()
        self.assertEqual(listing.public_url, '')
        self.assertNotIn(listing.master_sku, listing.public_url)
