from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.template.loader import render_to_string

from catalog.models import Product, ProductPriceInquiry, SellerProfile
from catalog.price_inquiries import process_price_inquiry_emails
from core.models import Request


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ProductPriceInquiryTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(title='Наконечник рулевой тяги', article='56820-4H100',
            seller_name='KOREA-PARTS', whatsapp_number='77001234567', price_on_request=True,
            status='active', slug='price-inquiry-test')
        self.url = reverse('product_price_inquiry', args=[self.product.pk])

    def payload(self, client=None):
        response = (client or self.client).get(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        return {'name': 'Тестовый покупатель', 'phone': '+7 (701) 234-56-78', 'quantity': '2',
            'comment': 'Есть в наличии?', 'consent': 'on',
            'submission': response.context['inquiry_form'].initial['submission']}

    def test_saved_once_owner_email_only_and_no_request_dispatch(self):
        data = self.payload()
        self.assertEqual(self.client.post(self.url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 200)
        self.assertEqual(self.client.post(self.url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 200)
        inquiry = ProductPriceInquiry.objects.get()
        self.assertEqual(inquiry.phone, '+77012345678')
        self.assertEqual(inquiry.quantity, 2)
        self.assertEqual(Request.objects.count(), 0)
        with patch('catalog.price_inquiries.send_mail', wraps=mail.send_mail) as sender:
            self.assertEqual(process_price_inquiry_emails(), 1)
            self.assertEqual(process_price_inquiry_emails(), 0)
            self.assertEqual(sender.call_count, 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['rkhaibullin@gmail.com'])
        for text in ['56820-4H100', 'KOREA-PARTS', '+77012345678', 'Количество: 2', 'Есть в наличии?']:
            self.assertIn(text, message.body)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.mail_status, 'sent')
        self.assertIsNotNone(inquiry.mail_sent_at)

    def test_invalid_phone_consent_and_quantity_do_not_save(self):
        data = self.payload()
        data.update(phone='+770123456789', quantity='0', consent='')
        response = self.client.post(self.url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()['errors']), {'phone', 'quantity', 'consent'})
        self.assertFalse(ProductPriceInquiry.objects.exists())

    def test_token_is_bound_to_session_and_product(self):
        data = self.payload()
        stranger = Client(); stranger.get(self.url)
        self.assertEqual(stranger.post(self.url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 400)
        other = Product.objects.create(title='Другая деталь', price_on_request=True, status='active')
        self.assertEqual(self.client.post(reverse('product_price_inquiry', args=[other.pk]), data,
            HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 400)
        self.assertFalse(ProductPriceInquiry.objects.exists())

    def test_expired_or_forged_token_rejected(self):
        data = self.payload(); data['submission'] += 'tampered'
        self.assertEqual(self.client.post(self.url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 400)
        self.assertFalse(ProductPriceInquiry.objects.exists())

    def test_ag_parts_and_priced_and_hidden_products_are_excluded(self):
        for attrs in [{'seller_name': 'AG Parts'}, {'price_on_request': False}, {'status': 'hidden'}]:
            with self.subTest(attrs=attrs):
                product = Product.objects.create(title='Не изменять', **attrs)
                self.assertEqual(self.client.get(reverse('product_price_inquiry', args=[product.pk])).status_code, 404)
        from django.contrib.auth import get_user_model
        seller = SellerProfile.objects.create(user=get_user_model().objects.create(username='ag'), name='AG Parts', slug='ag-parts')
        self.product.seller_profile = seller; self.product.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        html = render_to_string('catalog/includes/product_whatsapp_link.html', {'product': self.product})
        self.assertNotIn('data-price-inquiry', html)

    def test_link_does_not_require_seller_phone_and_other_links_stay_whatsapp(self):
        self.product.whatsapp_number = ''
        html = render_to_string('catalog/includes/product_whatsapp_link.html', {'product': self.product})
        self.assertIn('data-price-inquiry', html)
        self.assertIn(self.url, html)
        self.assertNotIn('target="_blank"', html)
        self.product.price_on_request = False; self.product.whatsapp_number = '77001234567'
        html = render_to_string('catalog/includes/product_whatsapp_link.html', {'product': self.product})
        self.assertNotIn('data-price-inquiry', html)
        self.assertIn(reverse('product_whatsapp_redirect', args=[self.product.pk]), html)

    def test_email_failure_keeps_request_and_requires_review_before_retry(self):
        self.client.post(self.url, self.payload())
        with patch('catalog.price_inquiries.send_mail', side_effect=OSError('smtp unavailable')) as sender:
            self.assertEqual(process_price_inquiry_emails(), 0)
            self.assertEqual(process_price_inquiry_emails(), 0)
            self.assertEqual(sender.call_count, 1)
        inquiry = ProductPriceInquiry.objects.get()
        self.assertEqual(inquiry.mail_status, 'failed')

    def test_csrf_and_no_javascript_fallback(self):
        strict = Client(enforce_csrf_checks=True)
        data = self.payload(strict)
        self.assertEqual(strict.post(self.url, data).status_code, 403)
        token = strict.cookies['csrftoken'].value
        response = strict.post(self.url, {**data, 'csrfmiddlewaretoken': token})
        self.assertContains(response, 'Запрос принят')
        self.assertEqual(ProductPriceInquiry.objects.count(), 1)

    @override_settings(REQUEST_OWNER_EMAIL_ASYNC=False)
    def test_committed_submission_uses_web_email_once(self):
        data = self.payload()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['rkhaibullin@gmail.com'])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(REQUEST_OWNER_EMAIL_ASYNC=True)
    @patch('core.services.operations_email_notifications._get_owner_email_executor')
    def test_async_submission_is_queued_after_commit(self, executor):
        data = self.payload()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            executor.return_value.submit.assert_not_called()
        self.assertEqual(response.status_code, 200)
        executor.return_value.submit.assert_called_once()
        self.assertEqual(len(mail.outbox), 0)
