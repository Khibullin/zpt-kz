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
