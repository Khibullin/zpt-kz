import json

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductPriceTier, SellerProfile, SellerWholesaleTerms
from orders.admin import OrderAdmin
from orders.constants import (
    CART_MODE_CONFLICT,
    CART_MODE_RETAIL,
    CART_MODE_WHOLESALE,
    SESSION_CART_MODE_KEY,
    SESSION_UTM_KEY,
)
from orders.email_notifications import build_order_email_body, send_order_admin_email
from orders.models import Order, OrderItem


RETAIL = 2500
WHOLESALE = 950
OTHER_WHOLESALE = 700


def _make_seller(username, name, phone, **kwargs):
    user = User.objects.create_user(username=username, password='secret12345')
    defaults = {
        'user': user,
        'name': name,
        'phone': phone,
        'city': 'Алматы',
        'address': 'г. Алматы, ул. Тестовая, 1',
        'wholesale_enabled': True,
        'wholesale_min_order_qty': 10,
    }
    defaults.update(kwargs)
    return SellerProfile.objects.create(**defaults)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_ADMIN_EMAIL='orders-admin@test.local',
)
class PublicWholesaleCartTests(TestCase):
    def setUp(self):
        self.seller = _make_seller(
            'wholesale-cart-owner',
            'AG Parts',
            '77771360740',
        )
        self.other_seller = _make_seller(
            'other-wholesale-owner',
            'Other Shop',
            '77001112233',
        )
        self.products = [
            self._product(self.seller, title='Haval cabin', article='WH-H1', slug='wh-h1'),
            self._product(self.seller, title='Chery cabin', article='WH-C1', slug='wh-c1'),
            self._product(self.seller, title='Changan cabin', article='WH-G1', slug='wh-g1'),
            self._product(self.seller, title='Spark plug', article='WH-S1', slug='wh-s1'),
        ]
        for product in self.products:
            ProductPriceTier.objects.create(product=product, min_qty=1, price=WHOLESALE)
        self.other_product = self._product(
            self.other_seller,
            title='Other wholesale',
            article='WH-O1',
            slug='wh-o1',
            phone='+77001112233',
        )
        ProductPriceTier.objects.create(
            product=self.other_product,
            min_qty=1,
            price=OTHER_WHOLESALE,
        )

    def _product(self, seller, **kwargs):
        phone = kwargs.pop('phone', '+77771360740')
        defaults = {
            'price': RETAIL,
            'seller_name': seller.name,
            'seller_profile': seller,
            'whatsapp_number': phone,
            'status': 'active',
            'publish_to_sellers': True,
            'city': 'Алматы',
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def _add(self, product, quantity=1, mode=CART_MODE_WHOLESALE):
        payload = {
            'product_id': product.id,
            'quantity': quantity,
        }
        if mode is not None:
            payload['mode'] = mode
        return self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _checkout_post(self, extra=None):
        data = {
            'customer_name': 'Иван',
            'customer_phone': '+7 (701) 123-45-67',
            'delivery_method': Order.DELIVERY_COURIER,
            'courier_street': 'Абая',
            'courier_house': '10',
        }
        if extra:
            data.update(extra)
        return self.client.post(reverse('orders:checkout'), data=data)

    def test_guest_wholesale_cart_uses_tier_price(self):
        response = self._add(self.products[0], quantity=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_total'], WHOLESALE * 3)
        self.assertEqual(
            self.client.session.get(SESSION_CART_MODE_KEY),
            CART_MODE_WHOLESALE,
        )
        cart = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart.context['cart_total'], WHOLESALE * 3)
        self.assertEqual(cart.context['items'][0]['unit_price'], WHOLESALE)
        self.assertTrue(cart.context['is_wholesale_cart'])
        html = cart.content.decode('utf-8')
        self.assertIn('Оптовая корзина', html)
        self.assertIn('В корзине 3 из 10 шт. Добавьте ещё 7 шт.', html)
        self.assertNotIn('Условия заказа', html)
        self.assertNotIn('цены с НДС', html)
        self.assertIn(
            reverse('public_seller_wholesale', kwargs={'slug': self.seller.slug}),
            html,
        )

    def test_retail_cart_stays_retail(self):
        response = self._add(self.products[0], quantity=3, mode=None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_total'], RETAIL * 3)
        self.assertEqual(
            self.client.session.get(SESSION_CART_MODE_KEY),
            CART_MODE_RETAIL,
        )
        cart = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart.context['items'][0]['unit_price'], RETAIL)
        self.assertFalse(cart.context['is_wholesale_cart'])
        self.assertEqual(
            cart.context['continue_shopping_url'],
            reverse('catalog_list'),
        )
        self.assertContains(cart, reverse('catalog_list'))

    def test_cannot_mix_retail_and_wholesale(self):
        first = self._add(self.products[0], quantity=1, mode=None)
        self.assertEqual(first.status_code, 200)
        mixed = self._add(self.products[1], quantity=1, mode=CART_MODE_WHOLESALE)
        self.assertEqual(mixed.status_code, 409)
        payload = mixed.json()
        self.assertEqual(payload['code'], CART_MODE_CONFLICT)
        self.assertIn('розничный заказ', payload['message'].lower())
        cart = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart.context['cart_count'], 1)
        self.assertFalse(cart.context['is_wholesale_cart'])

        self.client = self.client_class()
        first = self._add(self.products[0], quantity=1, mode=CART_MODE_WHOLESALE)
        self.assertEqual(first.status_code, 200)
        mixed = self._add(self.products[1], quantity=1, mode=None)
        self.assertEqual(mixed.status_code, 409)
        payload = mixed.json()
        self.assertEqual(payload['code'], CART_MODE_CONFLICT)
        self.assertIn('оптовый заказ', payload['message'].lower())
        cart = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart.context['cart_count'], 1)
        self.assertTrue(cart.context['is_wholesale_cart'])

    def test_cannot_add_other_seller(self):
        self._add(self.products[0], quantity=1)
        response = self._add(self.other_product, quantity=1)
        self.assertEqual(response.status_code, 409)
        self.assertIn('продавца', response.json()['message'])

    def test_mixed_qty_ten_allows_checkout(self):
        quantities = (2, 3, 2, 3)
        for product, qty in zip(self.products, quantities):
            response = self._add(product, quantity=qty)
            self.assertEqual(response.status_code, 200)
        cart = self.client.get(reverse('orders:cart'))
        self.assertTrue(cart.context['wholesale_status']['can_checkout'])
        self.assertEqual(cart.context['wholesale_status']['total_qty'], 10)
        checkout = self.client.get(reverse('orders:checkout'))
        self.assertEqual(checkout.status_code, 200)
        created = self._checkout_post()
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.order_type, Order.ORDER_TYPE_WHOLESALE)
        self.assertEqual(order.total_price, WHOLESALE * 10)
        items = list(OrderItem.objects.filter(order=order).order_by('product_id'))
        self.assertEqual(len(items), 4)
        self.assertEqual(sum(item.quantity for item in items), 10)
        for item in items:
            self.assertEqual(item.price_at_purchase, WHOLESALE)

    def test_nine_items_block_checkout(self):
        self._add(self.products[0], quantity=9)
        cart = self.client.get(reverse('orders:cart'))
        self.assertFalse(cart.context['wholesale_status']['can_checkout'])
        self.assertEqual(cart.context['wholesale_status']['remaining'], 1)
        get_checkout = self.client.get(reverse('orders:checkout'))
        self.assertEqual(get_checkout.status_code, 302)
        self.assertEqual(get_checkout.url, reverse('orders:cart'))
        self.assertEqual(Order.objects.count(), 0)

    def test_post_checkout_cannot_bypass_minimum(self):
        self._add(self.products[0], quantity=9)
        response = self._checkout_post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('orders:cart'))
        self.assertEqual(Order.objects.count(), 0)
        follow = self.client.get(reverse('orders:cart'))
        self.assertContains(follow, 'Добавьте ещё 1 шт. для оптового заказа')

    def test_wholesale_progress_and_continue_url(self):
        self._add(self.products[0], quantity=6)
        cart = self.client.get(reverse('orders:cart'))
        html = cart.content.decode('utf-8')
        self.assertIn('Оптовая корзина', html)
        self.assertIn('Минимальный заказ — 10 шт. в ассортименте', html)
        self.assertIn('В корзине 6 из 10 шт. Добавьте ещё 4 шт.', html)
        self.assertEqual(
            cart.context['continue_shopping_url'],
            reverse('public_seller_wholesale', kwargs={'slug': self.seller.slug}),
        )

        self._add(self.products[1], quantity=4)
        cart = self.client.get(reverse('orders:cart'))
        self.assertContains(cart, 'Минимальное количество набрано. Можно оформить заказ.')

    def test_retail_checkout_stores_retail_type(self):
        self._add(self.products[0], quantity=1, mode=None)
        created = self._checkout_post(extra={'order_type': Order.ORDER_TYPE_WHOLESALE})
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.order_type, Order.ORDER_TYPE_RETAIL)
        success = self.client.get(created.url)
        html = success.content.decode('utf-8')
        self.assertNotIn('Оптовый заказ', html)
        self.assertNotIn('Продолжить оптовые покупки', html)
        self.assertContains(success, 'Вернуться в каталог')

    def test_wholesale_success_and_email(self):
        for product, qty in zip(self.products, (2, 3, 2, 3)):
            self._add(product, quantity=qty)
        created = self._checkout_post()
        order = Order.objects.get()
        self.assertEqual(order.order_type, Order.ORDER_TYPE_WHOLESALE)
        success = self.client.get(created.url)
        html = success.content.decode('utf-8')
        self.assertIn('Оптовый заказ', html)
        self.assertIn('Продолжить оптовые покупки', html)
        self.assertIn(
            reverse('public_seller_wholesale', kwargs={'slug': self.seller.slug}),
            html,
        )
        self.assertIn('Написать продавцу в WhatsApp', html)
        self.assertIn('wa.me/77771360740', html)
        body = build_order_email_body(order)
        self.assertIn('Тип заказа: Оптовый', body)
        self.assertIn('Количество единиц: 10', body)
        send_order_admin_email(order.pk)
        self.assertTrue(mail.outbox)
        self.assertIn('ОПТОВЫЙ', mail.outbox[-1].subject)
        self.assertTrue(
            mail.outbox[-1].subject.startswith('Новый ОПТОВЫЙ заказ ZPT.KZ')
        )

    def test_retail_email_subject_stays_regular(self):
        self._add(self.products[0], quantity=1, mode=None)
        self._checkout_post()
        order = Order.objects.get()
        send_order_admin_email(order.pk)
        self.assertTrue(mail.outbox)
        subject = mail.outbox[-1].subject
        self.assertTrue(subject.startswith('Новый заказ ZPT.KZ'))
        self.assertNotIn('ОПТОВЫЙ', subject)

    def test_utm_snapshot_saved_and_cleared_after_order(self):
        storefront = reverse(
            'public_seller_wholesale',
            kwargs={'slug': self.seller.slug},
        )
        self.client.get(
            storefront,
            {
                'utm_source': 'whatsapp',
                'utm_medium': 'marketing',
                'utm_campaign': 'ag_parts_wholesale_launch',
            },
        )
        self.client.get(storefront)
        self.assertEqual(
            self.client.session[SESSION_UTM_KEY]['utm_source'],
            'whatsapp',
        )
        for product, qty in zip(self.products, (2, 3, 2, 3)):
            self._add(product, quantity=qty)
        self._checkout_post()
        order = Order.objects.get()
        self.assertEqual(order.utm_source, 'whatsapp')
        self.assertEqual(order.utm_medium, 'marketing')
        self.assertEqual(order.utm_campaign, 'ag_parts_wholesale_launch')
        self.assertNotIn(SESSION_UTM_KEY, self.client.session)

    def test_utm_values_are_truncated(self):
        storefront = reverse(
            'public_seller_wholesale',
            kwargs={'slug': self.seller.slug},
        )
        self.client.get(
            storefront,
            {'utm_campaign': 'c' * 400},
        )
        stored = self.client.session[SESSION_UTM_KEY]['utm_campaign']
        self.assertEqual(len(stored), 150)


def _configured_terms(seller, **kwargs):
    defaults = {
        'vat_mode': SellerWholesaleTerms.VAT_INCLUDED,
        'prepayment_percent': 100,
        'confirm_stock_before_payment': True,
        'provides_invoice': True,
        'provides_waybill': True,
        'provides_esf': True,
        'pickup_enabled': True,
        'pickup_city': 'Алматы',
        'delivery_kz_enabled': True,
        'delivery_payer': SellerWholesaleTerms.DELIVERY_PAYER_BUYER,
        'primary_carrier': 'DPD Kazakhstan',
        'primary_carrier_service': 'DPD OPTIMUM',
        'primary_carrier_url': 'https://dpd.kz/',
        'other_carrier_allowed': True,
        'stock_note': 'Наличие подтверждается перед оплатой.',
    }
    defaults.update(kwargs)
    return SellerWholesaleTerms.objects.create(seller=seller, **defaults)


class WholesaleTermsCartCheckoutTests(PublicWholesaleCartTests):
    def test_wholesale_cart_shows_conditions_block(self):
        _configured_terms(self.seller)
        self._add(self.products[0], quantity=3)
        cart = self.client.get(reverse('orders:cart'))
        html = cart.content.decode('utf-8')
        self.assertIn('Условия заказа', html)
        self.assertIn('цены с НДС', html)
        self.assertIn('100% предоплата', html)
        self.assertIn('наличие подтверждается перед оплатой', html)
        self.assertIn('доставка оплачивается покупателем', html)

    def test_retail_cart_has_no_wholesale_conditions(self):
        _configured_terms(self.seller)
        self._add(self.products[0], quantity=1, mode=None)
        cart = self.client.get(reverse('orders:cart'))
        html = cart.content.decode('utf-8')
        self.assertFalse(cart.context['is_wholesale_cart'])
        self.assertNotIn('Условия заказа', html)
        self.assertNotIn('цены с НДС', html)
        self.assertNotIn('100% предоплата', html)

    def test_wholesale_checkout_shows_carrier_and_prepayment(self):
        _configured_terms(self.seller)
        for product, qty in zip(self.products, (2, 3, 2, 3)):
            self._add(product, quantity=qty)
        checkout = self.client.get(reverse('orders:checkout'))
        self.assertEqual(checkout.status_code, 200)
        html = checkout.content.decode('utf-8')
        self.assertIn('После оформления заказа', html)
        self.assertIn('Менеджер подтверждает наличие.', html)
        self.assertIn('Вы получаете счет на 100% предоплату.', html)
        self.assertIn('Основная транспортная компания: DPD Kazakhstan', html)
        self.assertIn('Стоимость доставки оплачивает покупатель.', html)
        self.assertIn(
            'Другую транспортную компанию можно согласовать с продавцом.',
            html,
        )

    def test_retail_checkout_unchanged(self):
        _configured_terms(self.seller)
        self._add(self.products[0], quantity=1, mode=None)
        checkout = self.client.get(reverse('orders:checkout'))
        self.assertEqual(checkout.status_code, 200)
        html = checkout.content.decode('utf-8')
        self.assertNotIn('После оформления заказа', html)
        self.assertNotIn('Основная транспортная компания', html)
        self.assertNotIn('100% предоплату', html)
        self.assertIn('После оформления продавец свяжется с вами в WhatsApp', html)

    def test_wholesale_order_stores_terms_snapshot(self):
        terms = _configured_terms(self.seller)
        for product, qty in zip(self.products, (2, 3, 2, 3)):
            self._add(product, quantity=qty)
        created = self._checkout_post()
        order = Order.objects.get()
        snapshot = order.wholesale_terms_snapshot
        self.assertEqual(snapshot['vat_mode'], 'included')
        self.assertEqual(snapshot['prepayment_percent'], 100)
        self.assertTrue(snapshot['confirm_stock_before_payment'])
        self.assertTrue(snapshot['provides_invoice'])
        self.assertTrue(snapshot['provides_waybill'])
        self.assertTrue(snapshot['provides_esf'])
        self.assertTrue(snapshot['pickup_enabled'])
        self.assertEqual(snapshot['pickup_city'], 'Алматы')
        self.assertTrue(snapshot['delivery_kz_enabled'])
        self.assertEqual(snapshot['delivery_payer'], 'buyer')
        self.assertEqual(snapshot['primary_carrier'], 'DPD Kazakhstan')
        self.assertEqual(snapshot['primary_carrier_service'], 'DPD OPTIMUM')
        self.assertEqual(snapshot['primary_carrier_url'], 'https://dpd.kz/')
        self.assertTrue(snapshot['other_carrier_allowed'])
        terms.vat_mode = SellerWholesaleTerms.VAT_EXCLUDED
        terms.primary_carrier = 'Other Carrier'
        terms.prepayment_percent = 50
        terms.save()
        success = self.client.get(created.url)
        html = success.content.decode('utf-8')
        self.assertIn('Оплата', html)
        self.assertIn('100% предоплата после подтверждения наличия', html)
        self.assertIn('Менеджер свяжется с вами и выставит счет.', html)
        self.assertIn('Счет, накладная, ЭСФ', html)
        self.assertIn('Самовывоз — Алматы', html)
        self.assertIn('DPD Kazakhstan', html)
        self.assertIn('Стоимость доставки оплачивает покупатель.', html)
        self.assertNotIn('Other Carrier', html)
        self.assertNotIn('50%', html)
        self.assertNotIn('оплатите сейчас', html.lower())
        body = build_order_email_body(order)
        self.assertIn('Тип заказа: Оптовый', body)
        self.assertIn('НДС: включен в цену', body)
        self.assertIn('Оплата: 100% предоплата после подтверждения наличия', body)
        self.assertIn('Документы: счет, накладная, ЭСФ', body)
        self.assertIn('Доставка: DPD Kazakhstan / самовывоз Алматы', body)
        self.assertIn('Доставку оплачивает покупатель', body)
        self.assertNotIn('Other Carrier', body)
        display = OrderAdmin(Order, AdminSite()).wholesale_terms_snapshot_display(order)
        self.assertIn('НДС: включен в цену', str(display))
        self.assertIn('100%', str(display))
        self.assertNotIn('Other Carrier', str(display))

    def test_retail_order_snapshot_is_empty(self):
        _configured_terms(self.seller)
        self._add(self.products[0], quantity=1, mode=None)
        created = self._checkout_post()
        order = Order.objects.get()
        self.assertEqual(order.order_type, Order.ORDER_TYPE_RETAIL)
        self.assertEqual(order.wholesale_terms_snapshot, {})
        success = self.client.get(created.url)
        html = success.content.decode('utf-8')
        self.assertNotIn('100% предоплата', html)
        self.assertNotIn('Основная транспортная компания', html)
        body = build_order_email_body(order)
        self.assertNotIn('НДС: включен в цену', body)
        self.assertEqual(
            OrderAdmin(Order, AdminSite()).wholesale_terms_snapshot_display(order),
            '—',
        )


class WholesaleStockCartTests(PublicWholesaleCartTests):
    def test_stock_zero_blocks_wholesale_add(self):
        self.products[0].stock_qty = 0
        self.products[0].save(update_fields=['stock_qty'])
        response = self._add(self.products[0], quantity=1)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Недостаточно товара', response.json()['message'])

    def test_cannot_order_more_than_stock(self):
        self.products[0].stock_qty = 2
        self.products[0].save(update_fields=['stock_qty'])
        ok = self._add(self.products[0], quantity=2)
        self.assertEqual(ok.status_code, 200)
        too_many = self._add(self.products[0], quantity=1)
        self.assertEqual(too_many.status_code, 400)
        cart = self.client.get(reverse('orders:cart'))
        self.assertContains(cart, 'В наличии: 2 шт.')

    def test_null_stock_still_allows_wholesale_add(self):
        self.assertIsNone(self.products[0].stock_qty)
        response = self._add(self.products[0], quantity=3)
        self.assertEqual(response.status_code, 200)
        cart = self.client.get(reverse('orders:cart'))
        self.assertContains(cart, 'Наличие уточняется')


