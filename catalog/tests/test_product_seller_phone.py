from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, SellerProfile


class ProductDetailSellerPhoneTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='agparts-phone',
            password='secret12345',
        )
        self.seller = SellerProfile.objects.create(
            user=self.user,
            name='AG Parts',
            phone='77713607040',
            city='Алматы',
        )

    def test_uses_seller_profile_phone_instead_of_legacy_product_phone(self):
        product = Product.objects.create(
            title='Салонный фильтр Zeekr 001 — 8890649934',
            slug='ag-parts-seller-phone',
            article='8890649934',
            price=3839,
            seller_name=self.seller.name,
            seller_profile=self.seller,
            whatsapp_number='77771360740',
            status='active',
            city='Алматы',
        )
        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertContains(response, '+7 (771) 360-70-40')
        self.assertIn('https://wa.me/77713607040?', html)
        self.assertNotIn('https://wa.me/77771360740', html)
        self.assertNotContains(response, '+7 (777) 136-07-40')

    def test_legacy_product_without_seller_profile_uses_product_whatsapp(self):
        product = Product.objects.create(
            title='Legacy filter',
            slug='legacy-whatsapp-fallback',
            article='LEGACY-WA',
            price=1000,
            seller_name='Old Shop',
            seller_profile=None,
            whatsapp_number='77771360740',
            status='active',
            city='Алматы',
        )
        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertContains(response, '+7 (777) 136-07-40')
        self.assertIn('https://wa.me/77771360740?', html)
        self.assertNotIn('https://wa.me/77713607040', html)

    def test_leading_eight_product_phone_builds_international_wa_link(self):
        product = Product.objects.create(
            title='DPS filter',
            slug='dps-leading-eight-whatsapp',
            article='DPS-8777',
            price=1000,
            seller_name='DPS part\'s',
            seller_profile=None,
            whatsapp_number='87772320709',
            status='active',
            city='Алматы',
        )
        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('https://wa.me/77772320709', html)
        self.assertNotIn('https://wa.me/87772320709', html)

    def test_plus_seven_product_phone_keeps_international_wa_link(self):
        product = Product.objects.create(
            title='Plus seven filter',
            slug='plus-seven-whatsapp',
            article='PLUS-7777',
            price=1000,
            seller_name='Shop',
            seller_profile=None,
            whatsapp_number='+77772320709',
            status='active',
            city='Алматы',
        )
        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )
        html = response.content.decode()
        self.assertIn('https://wa.me/77772320709', html)

    def test_invalid_product_phone_does_not_render_wa_me(self):
        product = Product.objects.create(
            title='Broken phone filter',
            slug='invalid-whatsapp',
            article='BAD-PHONE',
            price=1000,
            seller_name='Shop',
            seller_profile=None,
            whatsapp_number='abc',
            status='active',
            city='Алматы',
        )
        response = self.client.get(
            reverse('product_detail', kwargs={'slug': product.slug})
        )
        html = response.content.decode()
        self.assertNotIn('https://wa.me/', html)
        self.assertNotIn('wa.me/None', html)
        self.assertNotIn('wa.me/abc', html)


class PublicSellerWhatsappTests(TestCase):
    def test_profile_leading_eight_builds_international_wa_link(self):
        user = User.objects.create_user(
            username='dps-profile-phone',
            password='secret12345',
        )
        seller = SellerProfile.objects.create(
            user=user,
            name="DPS part's",
            phone='87772320709',
            city='Алматы',
        )
        response = self.client.get(
            reverse('public_seller_profile', kwargs={'slug': seller.slug})
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('https://wa.me/77772320709', html)
        self.assertNotIn('https://wa.me/87772320709', html)

    def test_profile_invalid_phone_hides_wa_button(self):
        user = User.objects.create_user(
            username='bad-profile-phone',
            password='secret12345',
        )
        seller = SellerProfile.objects.create(
            user=user,
            name='Broken Shop',
            phone='abc',
            city='Алматы',
        )
        response = self.client.get(
            reverse('public_seller_profile', kwargs={'slug': seller.slug})
        )
        html = response.content.decode()
        self.assertNotIn('https://wa.me/', html)
        self.assertNotIn('seller-contact-wa-btn', html)
