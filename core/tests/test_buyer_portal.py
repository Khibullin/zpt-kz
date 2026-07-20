import os
import uuid

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.buyer_portal import (
    BUYER_STATUS_DIRECT,
    BUYER_STATUS_SENT,
    buyer_dispatch_status_label,
    build_request_sellers,
    buyer_history_url,
    home_page_url,
    new_request_url,
    repeat_request_path,
    request_page_url,
)
from core.models import (
    BuyerPortalAccess,
    Match,
    Request,
    RequestDispatch,
    Seller,
    WhatsAppMessageLog,
)


@override_settings(PUBLIC_BASE_URL='https://zpt.kz')
class BuyerPortalAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.phone_a = '77001112233'
        self.phone_b = '77009998877'
        self.req_a = Request.objects.create(
            transport_type='car',
            country='Китай',
            brand='Great Wall',
            model='Poer',
            category='Охлаждение',
            city='Алматы',
            phone=self.phone_a,
            status='sent',
        )
        self.portal_a = BuyerPortalAccess.objects.create(
            phone_normalized=self.phone_a,
        )
        self.req_b = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            city='Астана',
            phone=self.phone_b,
            status='sent',
        )
        self.portal_b = BuyerPortalAccess.objects.create(
            phone_normalized=self.phone_b,
        )
        self.seller = Seller.objects.create(
            name='Тестовый продавец',
            whatsapp='77005554433',
            transport_type='car',
            city='Алматы',
        )
        RequestDispatch.objects.create(
            request=self.req_a,
            seller=self.seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=timezone.now(),
            sent_at=timezone.now(),
        )

    def test_valid_request_token_returns_200(self):
        url = reverse(
            'view_request_status_public',
            kwargs={
                'req_id': self.req_a.id,
                'access_token': self.req_a.access_token,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Great Wall')
        self.assertContains(response, 'noindex,nofollow')

    def test_invalid_request_token_returns_404(self):
        url = reverse(
            'view_request_status_public',
            kwargs={
                'req_id': self.req_a.id,
                'access_token': uuid.uuid4(),
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_wrong_request_id_with_valid_token_returns_404(self):
        url = reverse(
            'view_request_status_public',
            kwargs={
                'req_id': self.req_b.id,
                'access_token': self.req_a.access_token,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_legacy_numeric_route_returns_404(self):
        url = reverse(
            'view_request_status_legacy_public',
            kwargs={'req_id': self.req_a.id},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_history_shows_only_same_phone_requests(self):
        Request.objects.create(
            transport_type='car',
            brand='Kia',
            model='Rio',
            category='Кузов',
            city='Алматы',
            phone=self.phone_a,
        )
        url = reverse(
            'view_buyer_request_history_public',
            kwargs={'access_token': self.portal_a.access_token},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Great Wall')
        self.assertContains(response, 'Kia')
        self.assertNotContains(response, 'Toyota')

    def test_history_excludes_other_buyer_requests(self):
        url = reverse(
            'view_buyer_request_history_public',
            kwargs={'access_token': self.portal_b.access_token},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Toyota')
        self.assertNotContains(response, 'Great Wall')

    def test_seller_list_from_request_dispatch(self):
        url = reverse(
            'view_request_status_public',
            kwargs={
                'req_id': self.req_a.id,
                'access_token': self.req_a.access_token,
            },
        )
        response = self.client.get(url)
        self.assertContains(response, 'Тестовый продавец')
        self.assertContains(response, 'Продавцы по вашей заявке')
        self.assertContains(response, 'Заявка отправлена продавцу')

    def test_sent_dispatch_status_label(self):
        url = reverse(
            'view_request_status_public',
            kwargs={
                'req_id': self.req_a.id,
                'access_token': self.req_a.access_token,
            },
        )
        response = self.client.get(url)
        self.assertContains(response, 'Заявка отправлена продавцу')
        self.assertNotContains(response, '>sent<')

    def test_repeat_request_url_contains_form_values(self):
        path = repeat_request_path(self.req_a)
        self.assertIn('brand=Great+Wall', path)
        self.assertIn('model=Poer', path)
        self.assertIn('category=', path)
        self.assertIn('city=', path)
        self.assertIn('phone=77001112233', path)
        self.assertTrue(path.startswith('/request-parts/?'))

    def test_url_helpers_use_public_domain(self):
        os.environ['PUBLIC_BASE_URL'] = 'https://zpt.kz'
        self.assertTrue(request_page_url(self.req_a).startswith('https://zpt.kz/my-request/'))
        self.assertTrue(buyer_history_url(self.req_a).startswith('https://zpt.kz/my-requests/'))
        self.assertEqual(home_page_url(), 'https://zpt.kz/')
        self.assertTrue(new_request_url().startswith('https://zpt.kz/request-parts/'))

    def test_request_page_url_includes_token(self):
        url = request_page_url(self.req_a)
        self.assertIn(str(self.req_a.access_token), url)
        self.assertIn(f'/my-request/{self.req_a.id}/', url)

    def test_history_page_has_noindex(self):
        url = reverse(
            'view_buyer_request_history_public',
            kwargs={'access_token': self.portal_a.access_token},
        )
        response = self.client.get(url)
        self.assertContains(response, 'noindex,nofollow')


@override_settings(PUBLIC_BASE_URL='https://zpt.kz')
class BuyerPortalSellerStatusTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )
        self.seller = Seller.objects.create(
            name='Status seller',
            whatsapp='77005554433',
            transport_type='car',
            city='Алматы',
        )
        self.dispatch = RequestDispatch.objects.create(
            request=self.request,
            seller=self.seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=timezone.now(),
            sent_at=timezone.now(),
        )
        self.page_url = reverse(
            'view_request_status_public',
            kwargs={
                'req_id': self.request.id,
                'access_token': self.request.access_token,
            },
        )

    def test_sent_dispatch_overrides_match_error_on_page(self):
        Match.objects.create(
            request=self.request,
            seller=self.seller,
            status='error',
        )
        response = self.client.get(self.page_url)
        self.assertContains(response, BUYER_STATUS_SENT)
        self.assertNotContains(response, 'Ошибка отправки')

    def test_failed_dispatch_shows_direct_contact_label(self):
        self.dispatch.status = RequestDispatch.STATUS_FAILED
        self.dispatch.save(update_fields=['status'])
        Match.objects.create(
            request=self.request,
            seller=self.seller,
            status='error',
        )
        response = self.client.get(self.page_url)
        self.assertContains(response, BUYER_STATUS_DIRECT)
        self.assertNotContains(response, 'Ошибка отправки')
        self.assertNotContains(response, 'Ошибка отправки WhatsApp')

    def test_successful_whatsapp_log_shows_sent_label(self):
        self.dispatch.status = RequestDispatch.STATUS_FAILED
        self.dispatch.save(update_fields=['status'])
        Match.objects.create(
            request=self.request,
            seller=self.seller,
            status='error',
        )
        WhatsAppMessageLog.objects.create(
            request_id=self.request.id,
            seller_name=self.seller.name,
            phone_clean='77005554433',
            is_success=True,
            status_text='sent',
            message_id='wamid.test-success',
        )
        response = self.client.get(self.page_url)
        self.assertContains(response, BUYER_STATUS_SENT)
        self.assertNotContains(response, 'Ошибка отправки')

    def test_public_page_has_no_whatsapp_error_text(self):
        self.dispatch.status = RequestDispatch.STATUS_FAILED
        self.dispatch.save(update_fields=['status'])
        Match.objects.create(
            request=self.request,
            seller=self.seller,
            status='error',
        )
        response = self.client.get(self.page_url)
        self.assertNotContains(response, 'Ошибка отправки WhatsApp')
        self.assertNotContains(response, 'Ошибка отправки')

    def test_more_than_eight_sellers_shows_show_more_button(self):
        for index in range(2, 11):
            seller = Seller.objects.create(
                name=f'Seller {index}',
                whatsapp=f'770055544{index:02d}',
                transport_type='car',
                city='Алматы',
            )
            RequestDispatch.objects.create(
                request=self.request,
                seller=seller,
                wave_number=1,
                position_number=index,
                status=RequestDispatch.STATUS_QUEUED,
                scheduled_at=timezone.now(),
            )

        sellers_data = build_request_sellers(self.request)
        self.assertGreater(sellers_data['total'], 8)
        self.assertGreater(sellers_data['hidden_count'], 0)

        response = self.client.get(self.page_url)
        self.assertContains(
            response,
            f'Показать ещё {sellers_data["hidden_count"]} продавцов',
        )
        self.assertEqual(
            response.content.count(b'class="seller-card is-hidden"'),
            sellers_data['hidden_count'],
        )

    def test_sellers_sorted_sent_before_pending_before_error(self):
        pending_seller = Seller.objects.create(
            name='Pending seller',
            whatsapp='77006661111',
            transport_type='car',
            city='Алматы',
        )
        failed_seller = Seller.objects.create(
            name='Failed seller',
            whatsapp='77006662222',
            transport_type='car',
            city='Алматы',
        )
        RequestDispatch.objects.create(
            request=self.request,
            seller=pending_seller,
            wave_number=1,
            position_number=2,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )
        RequestDispatch.objects.create(
            request=self.request,
            seller=failed_seller,
            wave_number=1,
            position_number=3,
            status=RequestDispatch.STATUS_FAILED,
            scheduled_at=timezone.now(),
        )

        sellers_data = build_request_sellers(self.request)
        names = [item['name'] for item in sellers_data['items']]
        self.assertEqual(names[0], 'Status seller')
        self.assertEqual(names[1], 'Pending seller')
        self.assertEqual(names[2], 'Failed seller')

    def test_buyer_dispatch_status_label_uses_shared_resolver(self):
        match = Match.objects.create(
            request=self.request,
            seller=self.seller,
            status='error',
        )
        self.assertEqual(
            buyer_dispatch_status_label(self.dispatch, match),
            BUYER_STATUS_SENT,
        )
