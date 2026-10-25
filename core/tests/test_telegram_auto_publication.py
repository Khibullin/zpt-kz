from unittest.mock import patch
from django.test import TestCase, override_settings
from django.db import transaction
from core.models import Request, RequestConsentEvent, TelegramRequestPublication
from core.services.telegram_auto_publication import record_submission_consent, process_new_submissions


@override_settings(TWO_WAY_REQUESTS_ENABLED=True)
class AutoPublicationTests(TestCase):
    def request(self, **kwargs):
        values = dict(brand='Toyota', model='Camry', city='Алматы',
                      description='Передний бампер', phone='77012223344', category='Кузов')
        values.update(kwargs)
        return Request.objects.create(**values)

    def consent(self, req):
        with transaction.atomic():
            record_submission_consent(req)

    @patch('core.services.telegram_auto_publication.publish')
    def test_new_consent_is_versioned_and_published_once(self, publish):
        def confirm(publication_id, actor):
            TelegramRequestPublication.objects.filter(pk=publication_id).update(status='published', message_id=123)
        publish.side_effect = confirm
        req = self.request()
        self.consent(req)
        self.assertEqual(RequestConsentEvent.objects.filter(request=req, text_version='submit_telegram_v1').count(), 2)
        self.assertEqual(process_new_submissions(), 1)
        self.assertEqual(process_new_submissions(), 0)
        publish.assert_called_once()
        pub = TelegramRequestPublication.objects.get(request=req)
        self.assertIn('Передний бампер', pub.text)
        self.assertNotIn(req.phone, pub.text)

    @patch('core.services.telegram_auto_publication.publish')
    def test_historical_requests_are_not_published(self, publish):
        self.request()
        self.assertEqual(process_new_submissions(), 0)
        publish.assert_not_called()

    @patch('core.services.telegram_auto_publication.publish')
    def test_contact_information_is_held_for_review(self, publish):
        for text in ['Бампер +7 701 222 33 44', 'Бампер, зовут Иван', 'Бампер test@example.kz', 'Бампер Иван Иванов', 'Бампер для Ивана']:
            self.consent(self.request(description=text))
        self.assertEqual(process_new_submissions(), 0)
        publish.assert_not_called()

    @patch('core.services.telegram_auto_publication.publish')
    def test_withdrawn_consent_and_closed_requests_are_not_published(self, publish):
        req = self.request()
        self.consent(req)
        Request.objects.filter(pk=req.pk).update(public_consent_at=None)
        req2 = self.request(purchase_result='closed')
        self.consent(req2)
        self.assertEqual(process_new_submissions(), 0)
        publish.assert_not_called()

    @override_settings(TWO_WAY_REQUESTS_ENABLED=False)
    @patch('core.services.telegram_auto_publication.publish')
    def test_feature_gate_is_respected(self, publish):
        self.consent(self.request())
        self.assertEqual(process_new_submissions(), 0)
        publish.assert_not_called()

    @patch('core.services.telegram_auto_publication.publish')
    def test_prepared_draft_survives_worker_restart(self, publish):
        req = self.request()
        self.consent(req)
        TelegramRequestPublication.objects.create(request=req, text='Передний бампер', reviewed=True)
        self.assertEqual(process_new_submissions(), 1)
        publish.assert_called_once()

    @patch('core.services.telegram_auto_publication.publish')
    def test_unknown_send_is_not_retried(self, publish):
        req = self.request()
        self.consent(req)
        TelegramRequestPublication.objects.create(request=req, text='Передний бампер', reviewed=True, status='unknown')
        self.assertEqual(process_new_submissions(), 0)
        publish.assert_not_called()

    def test_consent_rolls_back_with_submission(self):
        try:
            with transaction.atomic():
                req = self.request()
                self.consent(req)
                raise ValueError('failed submission')
        except ValueError:
            pass
        self.assertFalse(RequestConsentEvent.objects.exists())
        self.assertFalse(Request.objects.exists())

    @patch('core.services.telegram_auto_publication.publish')
    def test_abs_sensor_and_cyrillic_spelling_resume_held_drafts(self, publish):
        for text in ['Датчик ABS', 'Датчик АВС', 'Датчик АБС']:
            req = self.request(description=text)
            self.consent(req)
            TelegramRequestPublication.objects.create(request=req, text='', reviewed=False)
        self.assertEqual(process_new_submissions(), 3)
        self.assertEqual(publish.call_count, 3)
        self.assertEqual(TelegramRequestPublication.objects.filter(reviewed=True).count(), 3)

    @patch('core.services.telegram_auto_publication.publish')
    def test_held_private_text_stays_held(self, publish):
        req = self.request(description='Датчик ABS Иван Иванов')
        self.consent(req)
        TelegramRequestPublication.objects.create(request=req, text='', reviewed=False)
        self.assertEqual(process_new_submissions(), 0)
        self.assertEqual(process_new_submissions(), 0)
        self.assertEqual(TelegramRequestPublication.objects.count(), 1)
        publish.assert_not_called()

    @patch('core.services.telegram_auto_publication.publish')
    def test_manual_draft_is_not_rewritten(self, publish):
        from django.contrib.auth import get_user_model
        actor = get_user_model().objects.create_user(username='reviewer')
        req = self.request(description='Датчик ABS')
        self.consent(req)
        TelegramRequestPublication.objects.create(request=req, text='', reviewed=False, created_by=actor)
        self.assertEqual(process_new_submissions(), 0)
        publish.assert_not_called()
