from datetime import timedelta
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, SellerProfile
from core.models import (
    Request,
    RequestDispatch,
    Seller,
    SellerRequestAccess,
    SellerRequestPageEvent,
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
)
from orders.models import Order, WholesaleFunnelEvent
from orders.mvp_funnel import build_mvp_funnel_report


class MvpFunnelTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username='mvp-funnel-seller',
            password='TestPass123!',
        )
        self.profile = SellerProfile.objects.create(
            user=user,
            name='MVP Funnel Seller',
            phone='77015550123',
            city='Алматы',
        )
        self.product = Product.objects.create(
            title='Тестовая запчасть',
            slug='mvp-funnel-product',
            article='MVP-001',
            price=1000,
            status='active',
            seller_name=self.profile.name,
            whatsapp_number=self.profile.phone,
            seller_profile=self.profile,
        )

    def test_product_whatsapp_redirect_tracks_anonymous_click_without_pii(self):
        response = self.client.get(
            reverse('product_whatsapp_redirect', args=[self.product.pk]),
            {'src': 'card'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://wa.me/77015550123'))
        event = WholesaleFunnelEvent.objects.get(
            event_type=WholesaleFunnelEvent.EVENT_SELLER_WHATSAPP_CLICK,
        )
        self.assertEqual(event.product_id, self.product.pk)
        self.assertEqual(event.seller_profile_id, self.profile.pk)
        self.assertEqual(event.metadata, {'surface': 'card'})
        self.assertNotIn('phone', event.metadata)
        self.assertNotIn('whatsapp', event.metadata)

    def test_mvp_report_counts_product_whatsapp_request_contact_and_order(self):
        visitor = uuid.uuid4()
        WholesaleFunnelEvent.objects.create(
            event_type=WholesaleFunnelEvent.EVENT_PRODUCT_VIEW,
            seller_profile=self.profile,
            product=self.product,
            visitor_id=visitor,
        )
        WholesaleFunnelEvent.objects.create(
            event_type=WholesaleFunnelEvent.EVENT_SELLER_WHATSAPP_CLICK,
            seller_profile=self.profile,
            product=self.product,
            visitor_id=visitor,
            metadata={'surface': 'detail'},
        )

        req = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Подвеска',
            city='Алматы',
            phone='77015550999',
        )
        request_seller = Seller.objects.create(
            name='Request Seller',
            whatsapp='77015550124',
            transport_type='car',
            city='Алматы',
            receive_requests=True,
        )
        RequestDispatch.objects.create(
            request=req,
            seller=request_seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=timezone.now(),
            sent_at=timezone.now(),
        )
        access = SellerRequestAccess.objects.create(
            token='mvp-funnel-access-token',
            request=req,
            seller=request_seller,
            expires_at=timezone.now() + timedelta(days=1),
        )
        SellerRequestPageEvent.objects.create(
            access=access,
            request=req,
            seller=request_seller,
            event_type=SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
        )
        Order.objects.create(
            customer_name='Test Buyer',
            customer_phone='77015550888',
            seller_name=self.profile.name,
            seller_whatsapp=self.profile.phone,
            seller_profile=self.profile,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
            status=Order.STATUS_NEW,
        )

        today = timezone.localdate()
        report = build_mvp_funnel_report(today, today)

        self.assertEqual(report['product_viewers'], 1)
        self.assertEqual(report['whatsapp_clickers'], 1)
        self.assertEqual(report['whatsapp_clicks'], 1)
        self.assertEqual(report['requests'], 1)
        self.assertEqual(report['requests_sent'], 1)
        self.assertEqual(report['requests_with_seller_contact'], 1)
        self.assertEqual(report['orders'], 1)
        self.assertEqual(report['order_statuses'][0]['status'], Order.STATUS_NEW)
