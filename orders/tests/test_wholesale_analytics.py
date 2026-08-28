import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.db import DatabaseError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductPriceTier, SellerProfile
from orders.constants import (
    CART_MODE_CONFLICT,
    CART_MODE_WHOLESALE,
    SESSION_UTM_KEY,
    SESSION_WHOLESALE_VISITOR_KEY,
)
from orders.models import Order, OrderItem, WholesaleFunnelEvent
from orders.wholesale_analytics import (
    DIRECT_TRAFFIC_LABEL,
    EVENT_ADD_TO_CART,
    EVENT_CHECKOUT_VIEW,
    EVENT_ORDER_CREATED,
    EVENT_PRICE_DOWNLOAD,
    EVENT_PRODUCT_VIEW,
    EVENT_STOREFRONT_VIEW,
    build_wholesale_funnel_report,
    conversion_pct,
    format_conversion,
    get_wholesale_visitor_id,
    track_wholesale_event,
)


RETAIL = 2500
WHOLESALE = 950


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


class WholesaleAnalyticsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.seller = _make_seller(
            'analytics-owner',
            'Analytics Seller',
            '77015550001',
        )
        self.other_seller = _make_seller(
            'analytics-other',
            'Other Analytics',
            '77015550002',
        )
        self.product = self._product(
            self.seller,
            title='Cabin filter analytics',
            article='AN-CAB-1',
            slug='an-cab-1',
        )
        ProductPriceTier.objects.create(
            product=self.product,
            min_qty=1,
            price=WHOLESALE,
        )
        self.retail_product = self._product(
            self.seller,
            title='Retail only analytics',
            article='AN-RET-1',
            slug='an-ret-1',
            publish_to_sellers=False,
        )
        self.extra_products = [
            self._product(self.seller, title='P2', article='AN-2', slug='an-p2'),
            self._product(self.seller, title='P3', article='AN-3', slug='an-p3'),
            self._product(self.seller, title='P4', article='AN-4', slug='an-p4'),
        ]
        for product in self.extra_products:
            ProductPriceTier.objects.create(
                product=product,
                min_qty=1,
                price=WHOLESALE,
            )

    def _product(self, seller, **kwargs):
        defaults = {
            'price': RETAIL,
            'seller_name': seller.name,
            'seller_profile': seller,
            'whatsapp_number': seller.phone,
            'status': 'active',
            'publish_to_sellers': True,
            'city': 'Алматы',
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def _storefront_url(self, seller=None):
        seller = seller or self.seller
        return reverse('public_seller_wholesale', kwargs={'slug': seller.slug})

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

    def _checkout_post(self):
        return self.client.post(
            reverse('orders:checkout'),
            {
                'customer_name': 'Иван',
                'customer_phone': '+7 (701) 123-45-67',
                'delivery_method': Order.DELIVERY_COURIER,
                'courier_street': 'Абая',
                'courier_house': '10',
            },
        )

    def _fill_wholesale_cart(self):
        self._add(self.product, quantity=4)
        self._add(self.extra_products[0], quantity=2)
        self._add(self.extra_products[1], quantity=2)
        self._add(self.extra_products[2], quantity=2)

    def _anonymous_request(self, path='/'):
        request = self.factory.get(path)
        request.user = AnonymousUser()
        request.session = SessionStore()
        request.session.save()
        return request

    def test_visitor_creates_opaque_uuid_and_reuses_it(self):
        request = self._anonymous_request()
        first = get_wholesale_visitor_id(request)
        second = get_wholesale_visitor_id(request)
        self.assertEqual(first, second)
        self.assertIsInstance(first, uuid.UUID)
        self.assertNotEqual(str(first), request.session.session_key)
        self.assertEqual(
            request.session[SESSION_WHOLESALE_VISITOR_KEY],
            str(first),
        )

    def test_staff_not_tracked(self):
        staff = User.objects.create_user(
            username='staff-analytics',
            password='secret12345',
            is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(self._storefront_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)
        self.assertNotIn(SESSION_WHOLESALE_VISITOR_KEY, self.client.session)

    def test_storefront_get_creates_event_with_utm(self):
        response = self.client.get(
            self._storefront_url(),
            {
                'utm_source': 'whatsapp',
                'utm_medium': 'marketing',
                'utm_campaign': 'launch_test',
            },
        )
        self.assertEqual(response.status_code, 200)
        event = WholesaleFunnelEvent.objects.get()
        self.assertEqual(event.event_type, EVENT_STOREFRONT_VIEW)
        self.assertEqual(event.seller_profile_id, self.seller.pk)
        self.assertEqual(event.utm_source, 'whatsapp')
        self.assertEqual(event.utm_medium, 'marketing')
        self.assertEqual(event.utm_campaign, 'launch_test')
        self.assertEqual(event.metadata, {})

    def test_storefront_refresh_same_visitor_raw_events(self):
        self.client.get(self._storefront_url())
        self.client.get(self._storefront_url())
        events = list(WholesaleFunnelEvent.objects.all())
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].visitor_id, events[1].visitor_id)
        self.assertEqual(
            WholesaleFunnelEvent.objects.values('visitor_id').distinct().count(),
            1,
        )

    def test_storefront_head_and_disabled_seller_have_no_event(self):
        head = self.client.head(self._storefront_url())
        self.assertIn(head.status_code, (200, 405))
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)
        self.seller.wholesale_enabled = False
        self.seller.save(update_fields=['wholesale_enabled'])
        missing = self.client.get(self._storefront_url())
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)

    def test_product_view_only_for_wholesale_offer(self):
        wholesale = self.client.get(
            reverse('product_detail', kwargs={'slug': self.product.slug})
        )
        self.assertEqual(wholesale.status_code, 200)
        self.assertEqual(
            WholesaleFunnelEvent.objects.filter(event_type=EVENT_PRODUCT_VIEW).count(),
            1,
        )
        event = WholesaleFunnelEvent.objects.get(event_type=EVENT_PRODUCT_VIEW)
        self.assertEqual(event.product_id, self.product.pk)
        self.assertEqual(event.seller_profile_id, self.seller.pk)

        WholesaleFunnelEvent.objects.all().delete()
        retail = self.client.get(
            reverse('product_detail', kwargs={'slug': self.retail_product.slug})
        )
        self.assertEqual(retail.status_code, 200)
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)

    def test_price_download_creates_event(self):
        url = reverse(
            'public_seller_wholesale_price',
            kwargs={'slug': self.seller.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        event = WholesaleFunnelEvent.objects.get(event_type=EVENT_PRICE_DOWNLOAD)
        self.assertEqual(event.seller_profile_id, self.seller.pk)
        self.assertEqual(event.metadata, {})

    def test_successful_wholesale_add_creates_event(self):
        response = self._add(self.product, quantity=3)
        self.assertEqual(response.status_code, 200)
        event = WholesaleFunnelEvent.objects.get(event_type=EVENT_ADD_TO_CART)
        self.assertEqual(event.product_id, self.product.pk)
        self.assertEqual(event.quantity, 3)
        self.assertEqual(event.value_kzt, WHOLESALE * 3)

    def test_failed_stock_add_creates_no_event(self):
        self.product.stock_qty = 0
        self.product.save(update_fields=['stock_qty'])
        response = self._add(self.product, quantity=1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)

    def test_cart_mode_conflict_creates_no_event(self):
        retail_add = self._add(self.product, quantity=1, mode=None)
        self.assertEqual(retail_add.status_code, 200)
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)
        conflict = self._add(self.extra_products[0], quantity=1)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()['code'], CART_MODE_CONFLICT)
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)

    def test_retail_add_creates_no_wholesale_event(self):
        response = self._add(self.retail_product, quantity=1, mode=None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)

    def test_valid_wholesale_checkout_creates_checkout_view(self):
        self._fill_wholesale_cart()
        response = self.client.get(reverse('orders:checkout'))
        self.assertEqual(response.status_code, 200)
        event = WholesaleFunnelEvent.objects.get(event_type=EVENT_CHECKOUT_VIEW)
        self.assertEqual(event.seller_profile_id, self.seller.pk)
        self.assertEqual(event.quantity, 10)
        self.assertEqual(event.value_kzt, WHOLESALE * 10)

    def test_below_minimum_and_retail_checkout_have_no_checkout_event(self):
        self._add(self.product, quantity=1)
        blocked = self.client.get(reverse('orders:checkout'))
        self.assertEqual(blocked.status_code, 302)
        self.assertFalse(
            WholesaleFunnelEvent.objects.filter(event_type=EVENT_CHECKOUT_VIEW).exists()
        )

        self.client = self.client_class()
        WholesaleFunnelEvent.objects.all().delete()
        self._add(self.retail_product, quantity=1, mode=None)
        retail_checkout = self.client.get(reverse('orders:checkout'))
        self.assertEqual(retail_checkout.status_code, 200)
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)

    def test_wholesale_order_creates_one_order_event_with_order_utm(self):
        self.client.get(
            self._storefront_url(),
            {
                'utm_source': 'whatsapp',
                'utm_medium': 'marketing',
                'utm_campaign': 'launch_test',
            },
        )
        self._fill_wholesale_cart()
        created = self._checkout_post()
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.order_type, Order.ORDER_TYPE_WHOLESALE)
        events = WholesaleFunnelEvent.objects.filter(event_type=EVENT_ORDER_CREATED)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.order_id, order.pk)
        self.assertEqual(event.quantity, 10)
        self.assertEqual(event.value_kzt, order.total_price)
        self.assertEqual(event.utm_source, 'whatsapp')
        self.assertEqual(event.utm_medium, 'marketing')
        self.assertEqual(event.utm_campaign, 'launch_test')
        self.assertNotIn(SESSION_UTM_KEY, self.client.session)

        success = self.client.get(created.url)
        self.assertEqual(success.status_code, 200)
        self.assertEqual(
            WholesaleFunnelEvent.objects.filter(event_type=EVENT_ORDER_CREATED).count(),
            1,
        )

    def test_retail_order_has_no_wholesale_event(self):
        self._add(self.retail_product, quantity=1, mode=None)
        created = self._checkout_post()
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.order_type, Order.ORDER_TYPE_RETAIL)
        self.assertEqual(WholesaleFunnelEvent.objects.count(), 0)

    def test_analytics_failure_does_not_rollback_order(self):
        self._fill_wholesale_cart()
        with patch(
            'orders.wholesale_analytics.WholesaleFunnelEvent.objects.create',
            side_effect=DatabaseError('analytics down'),
        ):
            created = self._checkout_post()
        self.assertEqual(created.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(
            WholesaleFunnelEvent.objects.filter(event_type=EVENT_ORDER_CREATED).count(),
            0,
        )

    def test_duplicate_order_created_is_ignored(self):
        self._fill_wholesale_cart()
        self._checkout_post()
        order = Order.objects.get()
        request = self._anonymous_request()
        request.session[SESSION_WHOLESALE_VISITOR_KEY] = str(uuid.uuid4())
        track_wholesale_event(
            request,
            EVENT_ORDER_CREATED,
            self.seller,
            order=order,
            quantity=10,
            value_kzt=order.total_price,
        )
        self.assertEqual(
            WholesaleFunnelEvent.objects.filter(event_type=EVENT_ORDER_CREATED).count(),
            1,
        )

    def test_privacy_model_and_metadata(self):
        field_names = {field.name for field in WholesaleFunnelEvent._meta.fields}
        forbidden = {
            'ip',
            'ip_address',
            'user_agent',
            'phone',
            'email',
            'whatsapp',
            'customer_name',
            'customer_phone',
            'address',
            'delivery_address',
        }
        self.assertTrue(forbidden.isdisjoint(field_names))
        request = self._anonymous_request()
        event = track_wholesale_event(
            request,
            EVENT_STOREFRONT_VIEW,
            self.seller,
            metadata={
                'phone': '+77011234567',
                'email': 'buyer@example.com',
                'ip': '1.2.3.4',
                'user_agent': 'Mozilla',
                'page': 'storefront',
            },
        )
        self.assertIsNotNone(event)
        self.assertNotIn('phone', event.metadata)
        self.assertNotIn('email', event.metadata)
        self.assertNotIn('ip', event.metadata)
        self.assertNotIn('user_agent', event.metadata)
        self.assertEqual(event.metadata.get('page'), 'storefront')


class WholesaleFunnelReportTests(TestCase):
    def setUp(self):
        self.seller = _make_seller(
            'report-owner',
            'Report Seller',
            '77016660001',
        )
        self.other_seller = _make_seller(
            'report-other',
            'Report Other',
            '77016660002',
        )
        self.product = Product.objects.create(
            title='Report product',
            article='RP-1',
            slug='rp-1',
            price=5000,
            seller_name=self.seller.name,
            seller_profile=self.seller,
            whatsapp_number=self.seller.phone,
            status='active',
            publish_to_sellers=True,
            city='Алматы',
        )
        ProductPriceTier.objects.create(product=self.product, min_qty=1, price=800)
        self.admin = User.objects.create_superuser(
            'funnel-admin',
            'funnel-admin@test.local',
            'secret12345',
        )
        self.today = timezone.localdate()

    def _event(self, event_type, visitor, seller=None, **kwargs):
        return WholesaleFunnelEvent.objects.create(
            event_type=event_type,
            seller_profile=seller or self.seller,
            visitor_id=visitor,
            **kwargs,
        )

    def _order(self, total=8000, qty=10, **kwargs):
        defaults = {
            'customer_name': 'Buyer',
            'customer_phone': '+77010000000',
            'seller_name': self.seller.name,
            'status': Order.STATUS_NEW,
            'total_price': total,
            'delivery_method': Order.DELIVERY_COURIER,
            'order_type': Order.ORDER_TYPE_WHOLESALE,
        }
        defaults.update(kwargs)
        order = Order.objects.create(**defaults)
        OrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=qty,
            price_at_purchase=total // qty,
        )
        return order

    def test_distinct_visitor_kpi_ignores_refresh(self):
        visitor = uuid.uuid4()
        self._event(EVENT_STOREFRONT_VIEW, visitor)
        self._event(EVENT_STOREFRONT_VIEW, visitor)
        self._event(EVENT_STOREFRONT_VIEW, uuid.uuid4())
        report = build_wholesale_funnel_report(self.today, self.today)
        self.assertEqual(report['storefront'], 2)
        self.assertEqual(report['raw']['storefront'], 3)

    def test_conversion_percentages_and_zero_denominator(self):
        a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        for visitor in (a, b, c, d):
            self._event(EVENT_STOREFRONT_VIEW, visitor)
        for visitor in (a, b):
            self._event(EVENT_PRODUCT_VIEW, visitor)
        self._event(EVENT_ADD_TO_CART, a)
        self._event(EVENT_CHECKOUT_VIEW, a)
        order = self._order()
        self._event(
            EVENT_ORDER_CREATED,
            a,
            order=order,
            quantity=10,
            value_kzt=order.total_price,
        )
        report = build_wholesale_funnel_report(self.today, self.today)
        self.assertEqual(conversion_pct(2, 4), 50)
        self.assertEqual(report['conversions']['storefront_to_product'], 50)
        self.assertEqual(report['conversions']['product_to_cart'], 50)
        self.assertEqual(report['conversions']['cart_to_checkout'], 100)
        self.assertEqual(report['conversions']['checkout_to_order'], 100)
        self.assertEqual(report['conversions']['storefront_to_order'], 25)
        self.assertIsNone(conversion_pct(1, 0))
        self.assertEqual(format_conversion(None), '—')

        WholesaleFunnelEvent.objects.filter(event_type=EVENT_STOREFRONT_VIEW).delete()
        empty_storefront = build_wholesale_funnel_report(self.today, self.today)
        self.assertIsNone(empty_storefront['conversions']['storefront_to_product'])
        self.assertIsNone(empty_storefront['conversions']['storefront_to_order'])
        self.assertEqual(
            format_conversion(empty_storefront['conversions']['storefront_to_product']),
            '—',
        )

    def test_campaign_grouping_and_direct_traffic(self):
        wa = uuid.uuid4()
        direct = uuid.uuid4()
        self._event(
            EVENT_STOREFRONT_VIEW,
            wa,
            utm_source='whatsapp',
            utm_medium='marketing',
            utm_campaign='launch_a',
        )
        self._event(EVENT_STOREFRONT_VIEW, direct)
        order = self._order(total=1600, qty=2, utm_source='whatsapp', utm_campaign='launch_a')
        self._event(
            EVENT_ORDER_CREATED,
            wa,
            order=order,
            quantity=2,
            value_kzt=1600,
            utm_source='whatsapp',
            utm_medium='marketing',
            utm_campaign='launch_a',
        )
        report = build_wholesale_funnel_report(self.today, self.today)
        by_campaign = {row['utm_campaign']: row for row in report['campaigns']}
        self.assertIn('launch_a', by_campaign)
        self.assertEqual(by_campaign['launch_a']['storefront'], 1)
        self.assertEqual(by_campaign['launch_a']['orders'], 1)
        self.assertEqual(by_campaign['launch_a']['revenue'], 1600)
        self.assertEqual(by_campaign[DIRECT_TRAFFIC_LABEL]['storefront'], 1)
        self.assertEqual(by_campaign[DIRECT_TRAFFIC_LABEL]['orders'], 0)

    def test_order_revenue_uses_order_snapshot_not_current_price(self):
        order = self._order(total=7777, qty=7)
        self._event(
            EVENT_ORDER_CREATED,
            uuid.uuid4(),
            order=order,
            quantity=7,
            value_kzt=7777,
        )
        self.product.price = 1
        self.product.save(update_fields=['price'])
        report = build_wholesale_funnel_report(self.today, self.today)
        self.assertEqual(report['revenue'], 7777)
        self.assertEqual(report['items_qty'], 7)
        self.assertEqual(report['orders'], 1)

    def test_seller_date_and_campaign_filters(self):
        self._event(EVENT_STOREFRONT_VIEW, uuid.uuid4())
        self._event(
            EVENT_STOREFRONT_VIEW,
            uuid.uuid4(),
            seller=self.other_seller,
            utm_campaign='other_campaign',
        )
        old = self._event(EVENT_STOREFRONT_VIEW, uuid.uuid4())
        WholesaleFunnelEvent.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=40),
        )
        only_seller = build_wholesale_funnel_report(
            self.today,
            self.today,
            seller_id=self.seller.pk,
        )
        self.assertEqual(only_seller['storefront'], 1)
        campaign = build_wholesale_funnel_report(
            self.today,
            self.today,
            utm_campaign='other_campaign',
        )
        self.assertEqual(campaign['storefront'], 1)
        past = build_wholesale_funnel_report(
            self.today - timedelta(days=45),
            self.today - timedelta(days=35),
        )
        self.assertEqual(past['storefront'], 1)

    def test_price_download_unique_kpi(self):
        visitor = uuid.uuid4()
        self._event(EVENT_PRICE_DOWNLOAD, visitor)
        self._event(EVENT_PRICE_DOWNLOAD, visitor)
        self._event(EVENT_PRICE_DOWNLOAD, uuid.uuid4())
        report = build_wholesale_funnel_report(self.today, self.today)
        self.assertEqual(report['price_download'], 2)
        self.assertEqual(report['raw']['price_download'], 3)

    def test_admin_report_staff_only_and_shows_unique_kpis(self):
        visitor = uuid.uuid4()
        self._event(EVENT_STOREFRONT_VIEW, visitor)
        self._event(EVENT_STOREFRONT_VIEW, visitor)
        url = reverse('admin:orders_wholesalefunnelevent_funnel')
        anonymous = self.client.get(url)
        self.assertEqual(anonymous.status_code, 302)

        plain = User.objects.create_user('plain-user', password='secret12345')
        self.client.force_login(plain)
        forbidden = self.client.get(url)
        self.assertIn(forbidden.status_code, (302, 403))
        self.client.logout()

        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertIn('Оптовая аналитика', html)
        self.assertIn('Посетили оптовую витрину', html)
        self.assertIn('>1<', html)
        changelist = self.client.get(
            reverse('admin:orders_wholesalefunnelevent_changelist')
        )
        self.assertContains(changelist, 'Отчёт по воронке')
        orders = self.client.get(reverse('admin:orders_order_changelist'))
        self.assertContains(orders, 'Оптовая аналитика')
