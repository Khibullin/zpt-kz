import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from catalog.forms import ProductForm
from catalog.models import Product, SellerProfile


class PriceOnRequestTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='seller777',
            password='testpass123',
        )
        self.seller = SellerProfile.objects.create(
            user=self.user,
            name='Test Seller',
            phone='77770001122',
            city='Алматы',
        )
        self.product = Product.objects.create(
            title='Фильтр масляный',
            slug='filter-price-on-request',
            article='FIL-001',
            price=None,
            price_on_request=True,
            seller_name=self.seller.name,
            whatsapp_number=self.seller.phone,
            status='active',
            city='Алматы',
        )

    def test_product_form_clears_price_when_price_on_request_enabled(self):
        form = ProductForm(
            data={
                'title': 'Тест',
                'article': 'A-1',
                'price': '5000',
                'price_on_request': True,
                'condition': 'new',
                'status': 'active',
            }
        )
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data['price'])

    def test_product_form_requires_positive_price_without_flag(self):
        form = ProductForm(
            data={
                'title': 'Тест',
                'article': 'A-2',
                'price': '',
                'price_on_request': False,
                'condition': 'new',
                'status': 'active',
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn('price', form.errors)

    def test_whatsapp_message_asks_for_price(self):
        message = self.product.get_whatsapp_inquiry_message()
        self.assertIn('актуальную цену', message)
        self.assertIn('FIL-001', message)

    def test_cart_add_rejects_price_on_request_product(self):
        response = self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps({
                'product_id': self.product.id,
                'quantity': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload['success'])
        self.assertIn('WhatsApp', payload['error'])

    def test_catalog_card_shows_price_on_request_label(self):
        response = self.client.get(reverse('catalog_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Цена по запросу')
        self.assertContains(response, 'Узнать цену')
