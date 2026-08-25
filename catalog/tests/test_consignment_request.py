from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.admin import ProductConsignmentRequestAdmin
from catalog.models import (
    Product,
    ProductConsignment,
    ProductConsignmentRequest,
    SellerProfile,
)


def _seller(username='consign-seller', phone='77770000911'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name='Cons Shop',
        phone=phone,
        city='Алматы',
    )


def _product(**kwargs):
    defaults = {
        'title': 'Товар на реализацию',
        'price': 1500,
        'cost_price': 87654321,
        'seller_name': 'Cons Shop',
        'whatsapp_number': '+77770000911',
        'status': 'active',
        'slug': 'consign-product',
        'article': 'CONS-1',
        'stock_qty': 40,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class ConsignmentRequestTests(TestCase):
    def setUp(self):
        self.seller = _seller()
        self.product = _product()
        self.consignment = ProductConsignment.objects.create(
            product=self.product,
            enabled=True,
            max_qty=10,
            settlement_price=1100,
            term_days=14,
            conditions='Возврат остатка',
        )
        self.url = reverse('consignment_request_create')

    def _post(self, qty=5, product=None):
        return self.client.post(
            self.url,
            data={
                'product_id': (product or self.product).id,
                'quantity': qty,
            },
            content_type='application/json',
        )

    def test_guest_cannot_create_request(self):
        response = self._post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ProductConsignmentRequest.objects.count(), 0)

    def test_user_without_seller_profile_cannot_create(self):
        User.objects.create_user(username='plain-cons', password='secret12345')
        self.client.login(username='plain-cons', password='secret12345')
        response = self._post()
        self.assertEqual(response.status_code, 403)

    def test_seller_can_create_request(self):
        self.client.login(username='consign-seller', password='secret12345')
        response = self._post(qty=4)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['message'], 'Заявка на реализацию принята.')
        req = ProductConsignmentRequest.objects.get()
        self.assertEqual(req.requested_qty, 4)
        self.assertEqual(req.status, ProductConsignmentRequest.STATUS_NEW)
        self.assertEqual(self.product.stock_qty, 40)

    def test_disabled_consignment_rejected(self):
        self.consignment.enabled = False
        self.consignment.save(update_fields=['enabled'])
        self.client.login(username='consign-seller', password='secret12345')
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProductConsignmentRequest.objects.count(), 0)

    def test_max_qty_is_enforced(self):
        self.client.login(username='consign-seller', password='secret12345')
        response = self._post(qty=11)
        self.assertEqual(response.status_code, 400)
        self.assertIn('10', response.json()['error'])

    def test_stock_qty_is_enforced(self):
        self.product.stock_qty = 3
        self.product.save(update_fields=['stock_qty'])
        self.consignment.max_qty = 0
        self.consignment.save(update_fields=['max_qty'])
        self.client.login(username='consign-seller', password='secret12345')
        response = self._post(qty=4)
        self.assertEqual(response.status_code, 400)

    def test_snapshot_fields_are_stored(self):
        self.client.login(username='consign-seller', password='secret12345')
        self._post(qty=6)
        req = ProductConsignmentRequest.objects.get()
        self.assertEqual(req.settlement_price, 1100)
        self.assertEqual(req.term_days, 14)
        self.assertEqual(req.conditions, 'Возврат остатка')

    def test_later_consignment_change_does_not_change_snapshot(self):
        self.client.login(username='consign-seller', password='secret12345')
        self._post(qty=2)
        req = ProductConsignmentRequest.objects.get()
        self.consignment.settlement_price = 500
        self.consignment.term_days = 3
        self.consignment.conditions = 'Новые условия'
        self.consignment.save()
        req.refresh_from_db()
        self.assertEqual(req.settlement_price, 1100)
        self.assertEqual(req.term_days, 14)
        self.assertEqual(req.conditions, 'Возврат остатка')

    def test_admin_changelist_works(self):
        admin_user = User.objects.create_superuser(
            username='admin-cons',
            email='admin@example.com',
            password='secret12345',
        )
        ProductConsignmentRequest.objects.create(
            product=self.product,
            seller_profile=self.seller,
            requested_qty=2,
            settlement_price=1100,
            term_days=14,
            conditions='Возврат остатка',
        )
        self.client.force_login(admin_user)
        url = reverse('admin:catalog_productconsignmentrequest_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.title)
        self.assertTrue(
            {'status', 'created_at'} <= set(ProductConsignmentRequestAdmin.list_filter)
        )

    def test_request_does_not_create_order_or_cart(self):
        self.client.login(username='consign-seller', password='secret12345')
        self._post(qty=3)
        from orders.models import Order
        self.assertEqual(Order.objects.count(), 0)
        cart = self.client.get(reverse('orders:cart'))
        self.assertContains(cart, 'Корзина пуста')

    def test_cost_price_not_in_response(self):
        self.client.login(username='consign-seller', password='secret12345')
        response = self._post(qty=3)
        self.assertNotContains(response, '87654321')
        self.assertNotIn('cost_price', response.json())
