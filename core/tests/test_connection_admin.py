from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from core.models import Request, TelegramRequestPublication


@override_settings(TWO_WAY_REQUESTS_ENABLED=True, ALLOWED_HOSTS=['testserver'])
class PrepareAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='admin', email='test@example.test', password='password')
        self.client.force_login(self.user)
        self.req = Request.objects.create(phone='77000000000', brand='Mercedes', public_summary='ШРУСы', seller_feed_enabled=True,
            public_consent_at=timezone.now())
        self.url = reverse('admin:core_request_telegram', args=[self.req.pk])

    def test_get_previews_but_never_creates_or_sends(self):
        with patch('core.services.telegram_requests._call_api') as api:
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.assertFalse(TelegramRequestPublication.objects.exists())
            api.assert_not_called()

    def test_post_saves_draft_only_and_repeated_post_reuses_it(self):
        with patch('core.services.telegram_requests._call_api') as api:
            for _ in range(2):
                self.assertEqual(self.client.post(self.url, {'text': 'Нужны наружные ШРУСы', 'reviewed': 'on'}).status_code, 302)
            self.assertEqual(TelegramRequestPublication.objects.count(), 1)
            self.assertEqual(TelegramRequestPublication.objects.get().status, 'draft')
            api.assert_not_called()

    def test_no_consent_does_not_create_draft(self):
        self.req.public_consent_at = None
        self.req.save()
        response = self.client.post(self.url, {'text': 'Нужны ШРУСы', 'reviewed': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TelegramRequestPublication.objects.exists())

    def test_admin_request_page_and_publication_form_render(self):
        self.assertEqual(self.client.get(reverse('admin:core_request_change', args=[self.req.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:core_telegramrequestpublication_add')).status_code, 200)

    def test_staff_without_permissions_cannot_prepare(self):
        user = get_user_model().objects.create_user(username='limited', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_publications_and_attempt_history_cannot_be_deleted_from_admin(self):
        from django.db.models.deletion import ProtectedError
        from core.models import TelegramPublicationAttempt
        for state in ['draft', 'published', 'unknown']:
            with self.subTest(state=state):
                publication, _ = TelegramRequestPublication.objects.get_or_create(request=self.req, defaults={'text': 'ШРУС'})
                publication.status = state
                publication.save()
                TelegramPublicationAttempt.objects.get_or_create(publication=publication, result='started')
                url = reverse('admin:core_telegramrequestpublication_delete', args=[publication.pk])
                self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403)
                self.assertTrue(TelegramRequestPublication.objects.filter(pk=publication.pk).exists())
                with self.assertRaises(ProtectedError):
                    self.req.delete()

    def test_history_is_staff_only_and_distinguishes_contact_from_purchase(self):
        from core.models import Seller, RequestInteraction, RequestOutcomeEvent
        seller = Seller.objects.create(name='History seller', whatsapp='77001112233')
        RequestInteraction.objects.create(request=self.req, seller=seller, actor='seller', kind='whatsapp_click')
        RequestOutcomeEvent.objects.create(request=self.req, result='purchased', comment='Buyer statement')
        url = reverse('workflow_admin_control', args=[self.req.pk]) + '?view=history'
        contact = self.client.get(url + '&group=contact')
        self.assertContains(contact, 'Переход в WhatsApp')
        self.assertNotContains(contact, 'Покупатель подтвердил покупку')
        result = self.client.get(url + '&group=result')
        self.assertContains(result, 'Покупатель подтвердил покупку')
        self.assertNotContains(result, 'Переход в WhatsApp')
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_history_includes_delivery_and_followup_without_claiming_contact(self):
        from core.models import Seller, RequestSellerNotification, RequestBuyerFollowup
        seller = Seller.objects.create(name='Тестовый продавец', whatsapp='77001112233')
        RequestSellerNotification.objects.create(request=self.req, seller=seller, channel='telegram', status='sent', message_id=10)
        RequestBuyerFollowup.objects.create(request=self.req, channel='whatsapp', status='manual', actor=self.user)
        url = reverse('workflow_admin_control', args=[self.req.pk])
        response = self.client.get(url+'?view=history')
        self.assertContains(response, 'Заявка принята')
        self.assertContains(response, 'Уведомление продавцу: Принято Telegram')
        self.assertContains(response, 'Вопрос покупателю о результате')
        self.assertContains(response, 'Покупка этим не подтверждается')
        self.assertNotContains(response, str(self.req.access_token))
        self.assertNotContains(response, self.req.phone)
        self.assertNotContains(self.client.get(url+'?view=history&group=contact'), 'Уведомление продавцу:')
        self.assertNotContains(self.client.get(url+'?view=history&group=sellers'), 'Вопрос покупателю о результате')
        self.client.logout()
        self.assertNotEqual(self.client.get(url+'?view=history').status_code, 200)
