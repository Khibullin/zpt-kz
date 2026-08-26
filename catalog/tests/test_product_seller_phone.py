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
