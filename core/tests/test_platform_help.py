from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.html import strip_tags

from core.help_views import (
    platform_help_ask,
    platform_help_new_conversation,
    platform_help_transcribe,
)
from core.models import PlatformHelpConversation, PlatformHelpMessage, Seller
from core.platform_help import (
    OPENAI_RESPONSES_URL,
    OPENAI_TRANSCRIPTIONS_URL,
    PLATFORM_HELP_SYSTEM_PROMPT,
    normalize_help_contact_whatsapp,
    parse_help_output_text,
)
from core.services.platform_help_email import get_help_notification_email


FAKE_KEY = 'sk-test-platform-help-secret-key'
ANSWER_TEXT = 'Чтобы посмотреть заявки, откройте кабинет продавца.'
TRANSCRIPT_TEXT = 'Как добавить товар по артикулу?'


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _csrf_client():
    client = Client(enforce_csrf_checks=True)
    client.get(reverse('platform_help'))
    return client


def _csrf_headers(client):
    return {'HTTP_X_CSRFTOKEN': client.cookies['csrftoken'].value}


@override_settings(OPENAI_API_KEY=FAKE_KEY, HELP_EMAIL_ENABLED=False)
class PlatformHelpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    def _ask_mock(self, payload=None, status=200):
        body = payload if payload is not None else {'output_text': ANSWER_TEXT}

        def fake_post(url, **kwargs):
            fake_post.calls.append({'url': url, 'kwargs': kwargs})
            return FakeResponse(body, status=status)

        fake_post.calls = []
        return fake_post

    def test_help_page_renders(self):
        response = self.client.get('/request-parts/help/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Вопросы и справки ZPT.KZ')
        self.assertContains(response, 'id="help-input"')
        self.assertContains(response, 'id="help-mic"')
        self.assertContains(response, 'Задать вопрос голосом')
        self.assertContains(response, 'id="help-send"')
        self.assertContains(response, 'id="help-whatsapp"')
        self.assertContains(response, 'WhatsApp для ответа специалиста (необязательно)')
        self.assertNotContains(response, 'readonly')
        self.assertIn('csrftoken', response.cookies)

    def test_faq_still_works(self):
        response = self.client.get('/request-parts/faq/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Часто задаваемые вопросы')

    def test_go_help_points_to_new_page(self):
        response = self.client.get('/go/help/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/request-parts/help/')

    def test_other_go_routes_unchanged(self):
        expected = {
            'requests': '/request-parts/cabinet/',
            'add-product': '/market/seller/add/',
            'wholesale': '/market/?offer=wholesale&all=1',
            'sellers': '/parts-sellers/',
        }
        for destination, target in expected.items():
            with self.subTest(destination=destination):
                response = self.client.get(f'/go/{destination}/')
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, target)

    def test_unknown_go_destination_404(self):
        response = self.client.get('/go/not-a-real-destination/')
        self.assertEqual(response.status_code, 404)

    def test_go_help_post_405(self):
        response = self.client.post('/go/help/')
        self.assertEqual(response.status_code, 405)
        self.assertNotIn('Location', response)

    def test_valid_question_returns_answer_and_saves_history(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            response = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Где посмотреть мои заявки?'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['answer'], ANSWER_TEXT)
        self.assertNotIn(FAKE_KEY, response.content.decode('utf-8'))
        self.assertEqual(PlatformHelpConversation.objects.count(), 1)
        self.assertEqual(PlatformHelpMessage.objects.count(), 2)
        user_msg = PlatformHelpMessage.objects.get(role='user')
        assistant_msg = PlatformHelpMessage.objects.get(role='assistant')
        self.assertEqual(user_msg.content, 'Где посмотреть мои заявки?')
        self.assertEqual(user_msg.input_mode, 'text')
        self.assertEqual(assistant_msg.content, ANSWER_TEXT)
        call = fake_post.calls[0]
        self.assertEqual(call['url'], OPENAI_RESPONSES_URL)
        body = call['kwargs']['json']
        self.assertEqual(body['model'], settings.HELP_AI_MODEL)
        self.assertNotIn('tools', body)
        dumped = json.dumps(body)
        self.assertNotIn('web_search', dumped)
        self.assertEqual(body['input'][0]['role'], 'system')
        self.assertEqual(body['input'][0]['content'], PLATFORM_HELP_SYSTEM_PROMPT)
        self.assertEqual(body['input'][-1]['content'], 'Где посмотреть мои заявки?')

    def test_voice_input_mode_saved_when_explicit(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            response = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Как добавить товар?',
                    'input_mode': 'voice',
                }),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        user_msg = PlatformHelpMessage.objects.get(role='user')
        self.assertEqual(user_msg.input_mode, 'voice')

    def test_previous_db_history_passed_to_ai(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как добавить товар?'}),
                content_type='application/json',
            )
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'А где оптовые цены?'}),
                content_type='application/json',
            )
        second = fake_post.calls[1]['kwargs']['json']['input']
        contents = [item['content'] for item in second]
        self.assertIn('Как добавить товар?', contents)
        self.assertIn(ANSWER_TEXT, contents)
        self.assertEqual(second[-1]['content'], 'А где оптовые цены?')

    def test_client_conversation_id_is_ignored(self):
        other = PlatformHelpConversation.objects.create()
        PlatformHelpMessage.objects.create(
            conversation=other,
            role='user',
            content='Чужая история',
        )
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Где кабинет?',
                    'conversation_id': str(other.public_id),
                }),
                content_type='application/json',
            )
        self.assertEqual(PlatformHelpConversation.objects.count(), 2)
        own = PlatformHelpConversation.objects.exclude(pk=other.pk).get()
        self.assertFalse(own.messages.filter(content='Чужая история').exists())
        history = self.client.get(reverse('platform_help_history')).json()
        contents = [item['content'] for item in history['messages']]
        self.assertNotIn('Чужая история', contents)

    def test_malformed_json_400(self):
        response = self.client.post(
            reverse('platform_help_ask'),
            data='{not-json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])
        self.assertNotIn(FAKE_KEY, response.content.decode('utf-8'))

    def test_empty_question_400(self):
        response = self.client.post(
            reverse('platform_help_ask'),
            data=json.dumps({'message': '   '}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_too_long_question_400(self):
        response = self.client.post(
            reverse('platform_help_ask'),
            data=json.dumps({'message': 'а' * 2001}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(OPENAI_API_KEY='')
    def test_missing_api_key_safe_503(self):
        with patch('core.platform_help.requests.post') as mocked:
            response = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как войти?'}),
                content_type='application/json',
            )
        mocked.assert_not_called()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Traceback', response.content.decode('utf-8'))
        self.assertNotIn(FAKE_KEY, response.content.decode('utf-8'))

    def test_openai_timeout_keeps_user_message(self):
        import requests as requests_lib

        def boom(*args, **kwargs):
            raise requests_lib.Timeout('timed out')

        with patch('core.platform_help.requests.post', side_effect=boom):
            response = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как войти?'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(PlatformHelpMessage.objects.filter(role='user').count(), 1)
        self.assertEqual(PlatformHelpMessage.objects.filter(role='assistant').count(), 0)
        self.assertNotIn(FAKE_KEY, response.content.decode('utf-8'))

    def test_parser_output_text_and_fallback(self):
        self.assertEqual(
            parse_help_output_text({'output_text': ANSWER_TEXT}),
            ANSWER_TEXT,
        )
        self.assertEqual(
            parse_help_output_text({
                'output': [{
                    'type': 'message',
                    'content': [{'type': 'output_text', 'text': 'Запасной ответ'}],
                }],
            }),
            'Запасной ответ',
        )

    def test_history_empty_for_new_session(self):
        response = self.client.get(reverse('platform_help_history'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['messages'], [])

    def test_history_returns_exchange_and_is_session_scoped(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как добавить товар?'}),
                content_type='application/json',
            )
        history = self.client.get(reverse('platform_help_history')).json()
        roles = [item['role'] for item in history['messages']]
        self.assertEqual(roles, ['user', 'assistant'])
        other = Client()
        other_history = other.get(reverse('platform_help_history')).json()
        self.assertEqual(other_history['messages'], [])

    def test_new_conversation_clears_session_but_keeps_db_row(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как добавить товар?'}),
                content_type='application/json',
            )
        conversation = PlatformHelpConversation.objects.get()
        reset = self.client.post(reverse('platform_help_new_conversation'))
        self.assertEqual(reset.status_code, 200)
        self.assertTrue(PlatformHelpConversation.objects.filter(pk=conversation.pk).exists())
        history = self.client.get(reverse('platform_help_history')).json()
        self.assertEqual(history['messages'], [])

    def test_transcribe_webm_and_mp4(self):
        def fake_post(url, **kwargs):
            fake_post.calls.append(url)
            self.assertEqual(url, OPENAI_TRANSCRIPTIONS_URL)
            return FakeResponse({'text': TRANSCRIPT_TEXT})

        fake_post.calls = []
        with patch('core.platform_help.requests.post', fake_post):
            webm = self.client.post(
                reverse('platform_help_transcribe'),
                data={'audio': SimpleUploadedFile('voice.webm', b'webm-bytes', content_type='audio/webm')},
            )
            mp4 = self.client.post(
                reverse('platform_help_transcribe'),
                data={'audio': SimpleUploadedFile('voice.mp4', b'mp4-bytes', content_type='audio/mp4')},
            )
        self.assertEqual(webm.status_code, 200)
        self.assertEqual(webm.json()['text'], TRANSCRIPT_TEXT)
        self.assertEqual(mp4.status_code, 200)
        self.assertEqual(PlatformHelpMessage.objects.count(), 0)
        media_root = Path(settings.MEDIA_ROOT)
        if media_root.exists():
            leaked = [
                path for path in media_root.rglob('*')
                if path.is_file() and 'webm-bytes' in path.read_bytes().decode('latin1', errors='ignore')
            ]
            self.assertEqual(leaked, [])

    def test_transcribe_missing_audio_400(self):
        response = self.client.post(reverse('platform_help_transcribe'))
        self.assertEqual(response.status_code, 400)

    def test_transcribe_unsupported_mime_400(self):
        response = self.client.post(
            reverse('platform_help_transcribe'),
            data={'audio': SimpleUploadedFile('note.txt', b'hello', content_type='text/plain')},
        )
        self.assertEqual(response.status_code, 400)

    def test_transcribe_too_large_413(self):
        payload = b'x' * (8 * 1024 * 1024 + 1)
        response = self.client.post(
            reverse('platform_help_transcribe'),
            data={'audio': SimpleUploadedFile('huge.webm', payload, content_type='audio/webm')},
        )
        self.assertEqual(response.status_code, 413)

    def test_transcribe_openai_failure_503(self):
        import requests as requests_lib

        with patch('core.platform_help.requests.post', side_effect=requests_lib.Timeout('nope')):
            response = self.client.post(
                reverse('platform_help_transcribe'),
                data={'audio': SimpleUploadedFile('voice.webm', b'abc', content_type='audio/webm')},
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(FAKE_KEY, response.content.decode('utf-8'))
        self.assertEqual(PlatformHelpMessage.objects.count(), 0)

    @override_settings(HELP_ASK_MAX_PER_HOUR=1)
    def test_ask_rate_limit_429(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            first = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Первый'}),
                content_type='application/json',
            )
            second = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Второй'}),
                content_type='application/json',
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(len(fake_post.calls), 1)

    @override_settings(HELP_TRANSCRIBE_MAX_PER_HOUR=1)
    def test_transcribe_rate_limit_429(self):
        def fake_post(url, **kwargs):
            return FakeResponse({'text': TRANSCRIPT_TEXT})

        with patch('core.platform_help.requests.post', fake_post):
            first = self.client.post(
                reverse('platform_help_transcribe'),
                data={'audio': SimpleUploadedFile('a.webm', b'aaa', content_type='audio/webm')},
            )
            second = self.client.post(
                reverse('platform_help_transcribe'),
                data={'audio': SimpleUploadedFile('b.webm', b'bbb', content_type='audio/webm')},
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    def test_csrf_enforced_and_not_exempt(self):
        self.assertFalse(getattr(platform_help_ask, 'csrf_exempt', False))
        self.assertFalse(getattr(platform_help_transcribe, 'csrf_exempt', False))
        self.assertFalse(getattr(platform_help_new_conversation, 'csrf_exempt', False))
        csrf_client = Client(enforce_csrf_checks=True)
        blocked = csrf_client.post(
            reverse('platform_help_ask'),
            data=json.dumps({'message': 'Как войти?'}),
            content_type='application/json',
        )
        self.assertEqual(blocked.status_code, 403)
        allowed = _csrf_client()
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            ok = allowed.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как войти?'}),
                content_type='application/json',
                **_csrf_headers(allowed),
            )
        self.assertEqual(ok.status_code, 200)

    def test_models_and_admin_registered(self):
        self.assertTrue(site.is_registered(PlatformHelpConversation))
        self.assertTrue(site.is_registered(PlatformHelpMessage))
        user = User.objects.create_superuser('help-admin', 'a@b.c', 'secret')
        self.client.force_login(user)
        response = self.client.get(
            reverse('admin:core_platformhelpconversation_changelist')
        )
        self.assertEqual(response.status_code, 200)

    def test_authenticated_user_attached_to_conversation(self):
        user = User.objects.create_user('seller', password='secret')
        self.client.force_login(user)
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как войти?'}),
                content_type='application/json',
            )
        conversation = PlatformHelpConversation.objects.get()
        self.assertEqual(conversation.user_id, user.id)

    def test_normalize_help_contact_whatsapp(self):
        self.assertEqual(normalize_help_contact_whatsapp(''), '')
        self.assertEqual(normalize_help_contact_whatsapp('+7 701 123 45 67'), '77011234567')
        self.assertEqual(normalize_help_contact_whatsapp('8 701 123 45 67'), '77011234567')
        self.assertEqual(normalize_help_contact_whatsapp('7011234567'), '77011234567')
        with self.assertRaises(Exception) as caught:
            normalize_help_contact_whatsapp('12345')
        self.assertEqual(caught.exception.message, 'Проверьте номер WhatsApp.')

    def test_anonymous_without_whatsapp_keeps_blank_contact(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            response = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как войти?'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        conversation = PlatformHelpConversation.objects.get()
        self.assertEqual(conversation.contact_whatsapp, '')
        self.assertEqual(conversation.contact_source, '')

    def test_anonymous_plus7_whatsapp_is_normalized(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            response = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Как войти?',
                    'contact_whatsapp': '+7 701 123 45 67',
                }),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        conversation = PlatformHelpConversation.objects.get()
        self.assertEqual(conversation.contact_whatsapp, '77011234567')
        self.assertEqual(conversation.contact_source, 'user_input')
        dumped = json.dumps(fake_post.calls[0]['kwargs']['json'])
        self.assertNotIn('77011234567', dumped)
        self.assertNotIn('contact_whatsapp', dumped)

    def test_leading_eight_whatsapp_normalized(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Как войти?',
                    'contact_whatsapp': '8 701 123 45 67',
                }),
                content_type='application/json',
            )
        conversation = PlatformHelpConversation.objects.get()
        self.assertEqual(conversation.contact_whatsapp, '77011234567')

    def test_invalid_whatsapp_does_not_create_message(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            response = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Как войти?',
                    'contact_whatsapp': '12345',
                }),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['message'], 'Проверьте номер WhatsApp.')
        self.assertEqual(PlatformHelpMessage.objects.count(), 0)
        fake_post.calls = getattr(fake_post, 'calls', [])
        self.assertEqual(len(fake_post.calls), 0)

    def test_blank_contact_does_not_erase_saved_whatsapp(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Первый',
                    'contact_whatsapp': '+7 701 123 45 67',
                }),
                content_type='application/json',
            )
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Второй'}),
                content_type='application/json',
            )
        conversation = PlatformHelpConversation.objects.get()
        self.assertEqual(conversation.contact_whatsapp, '77011234567')
        self.assertEqual(conversation.contact_source, 'user_input')

    def test_user_can_update_own_whatsapp(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Первый',
                    'contact_whatsapp': '+7 701 123 45 67',
                }),
                content_type='application/json',
            )
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Второй',
                    'contact_whatsapp': '+7 702 999 88 77',
                }),
                content_type='application/json',
            )
        conversation = PlatformHelpConversation.objects.get()
        self.assertEqual(conversation.contact_whatsapp, '77029998877')

    def test_seller_whatsapp_taken_from_profile_and_payload_ignored(self):
        user = User.objects.create_user('ag-parts', password='secret')
        Seller.objects.create(
            name='AG Parts',
            whatsapp='77700001122',
            city='Алматы',
            transport_type='car',
            user=user,
        )
        self.client.force_login(user)
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            response = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Как войти?',
                    'contact_whatsapp': '+7 701 123 45 67',
                }),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        conversation = PlatformHelpConversation.objects.get()
        self.assertEqual(conversation.contact_whatsapp, '77700001122')
        self.assertEqual(conversation.contact_source, 'seller')
        dumped = json.dumps(fake_post.calls[0]['kwargs']['json'])
        self.assertNotIn('77700001122', dumped)
        self.assertNotIn('77011234567', dumped)

    def test_transcribe_does_not_save_whatsapp(self):
        with patch('core.platform_help.requests.post', return_value=FakeResponse({'text': TRANSCRIPT_TEXT})):
            response = self.client.post(
                reverse('platform_help_transcribe'),
                data={'audio': SimpleUploadedFile('voice.webm', b'abc', content_type='audio/webm')},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PlatformHelpConversation.objects.count(), 0)
        self.assertEqual(PlatformHelpMessage.objects.count(), 0)

    def test_history_payload_omits_contact_whatsapp(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Как войти?',
                    'contact_whatsapp': '+7 701 123 45 67',
                }),
                content_type='application/json',
            )
        history = self.client.get(reverse('platform_help_history')).json()
        dumped = json.dumps(history)
        self.assertNotIn('contact_whatsapp', dumped)
        self.assertNotIn('77011234567', dumped)

    def test_new_conversation_keeps_old_whatsapp_in_db(self):
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({
                    'message': 'Первый',
                    'contact_whatsapp': '+7 701 123 45 67',
                }),
                content_type='application/json',
            )
        old = PlatformHelpConversation.objects.get()
        self.client.post(reverse('platform_help_new_conversation'))
        with patch('core.platform_help.requests.post', fake_post):
            self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Второй'}),
                content_type='application/json',
            )
        self.assertEqual(PlatformHelpConversation.objects.count(), 2)
        old.refresh_from_db()
        self.assertEqual(old.contact_whatsapp, '77011234567')
        new = PlatformHelpConversation.objects.exclude(pk=old.pk).get()
        self.assertEqual(new.contact_whatsapp, '')

    def test_help_page_seller_whatsapp_is_readonly(self):
        user = User.objects.create_user('ag-parts-page', password='secret')
        Seller.objects.create(
            name='AG Parts',
            whatsapp='77700001122',
            city='Алматы',
            transport_type='car',
            user=user,
        )
        self.client.force_login(user)
        response = self.client.get('/request-parts/help/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="help-whatsapp"')
        self.assertContains(response, 'readonly')
        self.assertContains(response, '77700001122')
        self.assertContains(
            response,
            'Для личного ответа используется WhatsApp из вашего профиля продавца.',
        )

    def test_admin_conversation_shows_whatsapp_reply_link(self):
        conversation = PlatformHelpConversation.objects.create(
            contact_whatsapp='77011234567',
            contact_source='user_input',
        )
        admin_user = User.objects.create_superuser('help-wa-admin', 'a@b.c', 'secret')
        self.client.force_login(admin_user)
        response = self.client.get(
            reverse('admin:core_platformhelpconversation_change', args=[conversation.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '77011234567')
        self.assertContains(response, 'Открыть WhatsApp')
        self.assertContains(response, 'https://wa.me/77011234567')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'noopener')


EMAIL_SETTINGS = {
    'OPENAI_API_KEY': FAKE_KEY,
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    'HELP_EMAIL_ENABLED': True,
    'HELP_NOTIFICATION_EMAIL': 'help-admin@test.local',
    'ORDER_ADMIN_EMAIL': 'orders-admin@test.local',
    'EMAIL_HOST_USER': 'smtp-user@test.local',
    'EMAIL_HOST_PASSWORD': 'smtp-secret-password',
    'PUBLIC_BASE_URL': 'https://zpt.kz',
}


@override_settings(**EMAIL_SETTINGS)
class PlatformHelpEmailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        mail.outbox.clear()

    def _ask_mock(self, payload=None, status=200):
        body = payload if payload is not None else {'output_text': ANSWER_TEXT}

        def fake_post(url, **kwargs):
            fake_post.calls.append({'url': url, 'kwargs': kwargs})
            return FakeResponse(body, status=status)

        fake_post.calls = []
        return fake_post

    def _ask(self, message, input_mode=None, client=None, **extra):
        payload = {'message': message}
        if input_mode is not None:
            payload['input_mode'] = input_mode
        payload.update(extra)
        http = client or self.client
        fake_post = self._ask_mock()
        with patch('core.platform_help.requests.post', fake_post):
            response = http.post(
                reverse('platform_help_ask'),
                data=json.dumps(payload),
                content_type='application/json',
            )
        return response, fake_post

    def _html(self, email):
        self.assertTrue(email.alternatives)
        content, mimetype = email.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        return content

    def test_successful_text_question_sends_one_email(self):
        question = 'Как добавить товар по артикулу?'
        response, _fake = self._ask(question)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['help-admin@test.local'])
        self.assertEqual(email.subject, 'ZPT.KZ: новый вопрос — Вопросы и справки')
        self.assertNotIn(question, email.subject)
        body = email.body
        self.assertIn(question, body)
        self.assertIn(ANSWER_TEXT, body)
        self.assertIn('Способ ввода: текст', body)
        self.assertIn('Тип пользователя: Анонимный пользователь', body)
        self.assertIn('WhatsApp для ответа: не указан', body)
        html = self._html(email)
        self.assertIn('Тип пользователя: Анонимный пользователь', html)
        self.assertIn('WhatsApp для ответа: не указан', html)
        self.assertNotIn('Ответить в WhatsApp', html)
        conversation = PlatformHelpConversation.objects.get()
        user_message = PlatformHelpMessage.objects.get(role='user')
        self.assertIn(str(conversation.public_id), body)
        self.assertIn(
            f'https://zpt.kz/admin/core/platformhelpconversation/{conversation.pk}/change/',
            body,
        )
        self.assertIn(
            f'https://zpt.kz/admin/core/platformhelpmessage/{user_message.pk}/change/',
            body,
        )
        self.assertNotIn(FAKE_KEY, body)
        self.assertNotIn('smtp-secret-password', body)
        self.assertNotIn(FAKE_KEY, email.subject)
        self.assertNotIn('sessionid', body)
        self.assertNotIn('csrftoken', body)
        self.assertNotIn('csrfmiddlewaretoken', body)

    def test_voice_question_email_marks_voice_input(self):
        response, _fake = self._ask('Как добавить товар?', input_mode='voice')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Способ ввода: голос', mail.outbox[0].body)

    def test_authenticated_seller_email_includes_shop_details(self):
        user = User.objects.create_user('ag-parts', password='secret')
        Seller.objects.create(
            name='AG Parts',
            whatsapp='77700001122',
            city='Алматы',
            transport_type='car',
            user=user,
        )
        self.client.force_login(user)
        response, _fake = self._ask('Как добавить товар по артикулу?')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(
            email.subject,
            'ZPT.KZ: новый вопрос продавца — AG Parts',
        )
        body = email.body
        self.assertIn('Тип пользователя: Продавец', body)
        self.assertIn('Продавец: AG Parts', body)
        self.assertIn('WhatsApp: 77700001122', body)
        self.assertIn('Город: Алматы', body)
        self.assertIn('WhatsApp для ответа: +77700001122', body)
        self.assertIn('Источник контакта: Профиль продавца', body)
        self.assertIn('https://wa.me/77700001122', body)
        self.assertNotIn('?text=', body)
        reply_line = next(
            line for line in body.splitlines() if line.startswith('https://wa.me/')
        )
        self.assertNotIn('Как добавить товар по артикулу?', reply_line)
        html = self._html(email)
        self.assertIn('Тип пользователя: Продавец', html)
        self.assertIn('Продавец: AG Parts', html)
        self.assertIn('Ответить в WhatsApp', html)
        self.assertIn('https://wa.me/77700001122?text=', html)
        self.assertNotIn('?text=', strip_tags(html))

    def test_openai_failure_still_sends_email(self):
        import requests as requests_lib

        with patch('core.platform_help.requests.post', side_effect=requests_lib.Timeout('timed out')):
            response = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как войти?'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn('Как войти?', body)
        self.assertIn('ОТВЕТ ИИ:', body)
        self.assertIn('Не получен: ИИ временно недоступен или произошла ошибка ответа.', body)
        html = self._html(mail.outbox[0])
        self.assertIn('Не получен: ИИ временно недоступен или произошла ошибка ответа.', html)
        self.assertEqual(mail.outbox[0].alternatives[0][1], 'text/html')

    def test_smtp_exception_does_not_change_successful_api(self):
        with patch(
            'core.services.platform_help_email.send_mail',
            side_effect=OSError('smtp down'),
        ):
            response, _fake = self._ask('Как войти?')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(response.json()['answer'], ANSWER_TEXT)
        self.assertEqual(len(mail.outbox), 0)

    def test_smtp_exception_on_ai_failure_keeps_503(self):
        import requests as requests_lib

        with patch('core.platform_help.requests.post', side_effect=requests_lib.Timeout('timed out')):
            with patch(
                'core.services.platform_help_email.send_mail',
                side_effect=OSError('smtp down'),
            ):
                response = self.client.post(
                    reverse('platform_help_ask'),
                    data=json.dumps({'message': 'Как войти?'}),
                    content_type='application/json',
                )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['ok'])
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(**{**EMAIL_SETTINGS, 'HELP_EMAIL_ENABLED': False})
    def test_disabled_setting_does_not_send_email(self):
        response, _fake = self._ask('Как войти?')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(**{**EMAIL_SETTINGS, 'HELP_NOTIFICATION_EMAIL': ''})
    def test_empty_help_email_falls_back_to_order_admin(self):
        self.assertEqual(get_help_notification_email(), 'orders-admin@test.local')
        response, _fake = self._ask('Как войти?')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['orders-admin@test.local'])

    def test_transcribe_does_not_send_email(self):
        with patch('core.platform_help.requests.post', return_value=FakeResponse({'text': TRANSCRIPT_TEXT})):
            response = self.client.post(
                reverse('platform_help_transcribe'),
                data={'audio': SimpleUploadedFile('voice.webm', b'abc', content_type='audio/webm')},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_questions_do_not_send_email(self):
        empty = self.client.post(
            reverse('platform_help_ask'),
            data=json.dumps({'message': '   '}),
            content_type='application/json',
        )
        malformed = self.client.post(
            reverse('platform_help_ask'),
            data='{not-json',
            content_type='application/json',
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_anonymous_whatsapp_email_contains_reply_link(self):
        question = 'Как добавить товар по артикулу?'
        response, _fake = self._ask(question, contact_whatsapp='+7 701 123 45 67')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn('WhatsApp для ответа: +77011234567', body)
        self.assertIn('Источник контакта: Указан пользователем', body)
        self.assertIn('https://wa.me/77011234567', body)
        self.assertNotIn('?text=', body)
        reply_line = next(
            line for line in body.splitlines() if line.startswith('https://wa.me/')
        )
        self.assertNotIn(question, reply_line)
        html = self._html(mail.outbox[0])
        self.assertIn('Ответить в WhatsApp', html)
        match = re.search(r'href="(https://wa\.me/77011234567\?text=[^"]*)"', html)
        self.assertIsNotNone(match)
        self.assertNotIn(question, match.group(1))
        self.assertNotIn('?text=', strip_tags(html))

    def test_html_email_escapes_user_content(self):
        payload = '<script>alert(1)</script><b>x</b>'
        response, _fake = self._ask(payload, contact_whatsapp='+7 701 123 45 67')
        self.assertEqual(response.status_code, 200)
        html = self._html(mail.outbox[0])
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;b&gt;', html)
        self.assertNotIn('<b>x</b>', html)

    def test_invalid_whatsapp_does_not_send_email(self):
        response = self.client.post(
            reverse('platform_help_ask'),
            data=json.dumps({
                'message': 'Как войти?',
                'contact_whatsapp': '12345',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(PlatformHelpMessage.objects.count(), 0)
