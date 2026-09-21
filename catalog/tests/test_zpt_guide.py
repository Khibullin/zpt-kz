"""ZPT Гид page, redirects, FAQ source and homepage showcase limit."""
from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, SellerProfile
from core.models import Feedback, Request
from core.platform_help import platform_help_system_prompt
from core.zpt_guide_faq import FAQ_SECTIONS, faq_item_count, faq_prompt_block


class ZptGuideFaqSourceTests(TestCase):
    def test_faq_has_exactly_thirty_verified_questions(self):
        self.assertEqual(faq_item_count(), 30)
        questions = [item['question'] for _, item in _iter_items()]
        self.assertEqual(len(set(questions)), 30)
        prompt = faq_prompt_block()
        self.assertIn('ZPT Гид', prompt)
        self.assertNotIn('GPT Guide', prompt)
        self.assertNotIn('GPT Гид', prompt)
        combined = platform_help_system_prompt()
        self.assertIn('Как оставить запрос на запчасть?', combined)
        self.assertIn(prompt, combined)

    def test_kit_answers_do_not_claim_oil_in_every_kit(self):
        answers = ' '.join(item['answer'] for _, item in _iter_items())
        self.assertIn('Масло не входит во все наборы', answers)
        self.assertIn('Свечи входят не во все комплекты', answers)


def _iter_items():
    for section in FAQ_SECTIONS:
        for item in section['items']:
            yield section, item


class ZptGuidePageTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_guide_page_renders_helper_faq_feedback_and_install(self):
        response = self.client.get(reverse('zpt_gid'))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ZPT Гид')
        self.assertContains(response, 'Чем помочь?')
        self.assertContains(response, 'id="help-input"')
        self.assertContains(response, 'Задать вопрос голосом')
        self.assertContains(response, 'Передать вопрос менеджеру')
        self.assertContains(response, 'Связаться с нами')
        self.assertContains(response, 'Добавить ZPT на главный экран')
        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="phone"')
        self.assertContains(response, 'name="message"')
        self.assertContains(response, 'Как оставить запрос на запчасть?')
        self.assertContains(response, 'Как добавить ZPT на главный экран телефона?')
        self.assertEqual(html.count('data-faq-item'), 30)
        self.assertNotContains(response, 'GPT Guide')
        self.assertNotContains(response, 'GPT Гид')
        self.assertIn('csrftoken', response.cookies)

    def test_old_public_addresses_redirect_without_touching_post_apis(self):
        faq = self.client.get('/faq/')
        self.assertEqual(faq.status_code, 302)
        self.assertEqual(faq.url, '/zpt-gid/#spravka')

        help_page = self.client.get('/request-parts/help/')
        self.assertEqual(help_page.status_code, 302)
        self.assertEqual(help_page.url, '/zpt-gid/#pomoshchnik')

        feedback = self.client.get('/feedback/')
        self.assertEqual(feedback.status_code, 302)
        self.assertEqual(feedback.url, '/zpt-gid/#svyaz')

        ask = self.client.post('/api/platform-help/ask/', data='{}', content_type='application/json')
        self.assertNotEqual(ask.status_code, 302)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_feedback_post_saves_and_success_only_after_accept(self):
        response = self.client.post(
            reverse('feedback'),
            {
                'name': 'Тест',
                'phone': '77011234567',
                'message': 'Проверьте заявку на главной',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertEqual(Feedback.objects.count(), 1)
        self.assertEqual(Feedback.objects.get().message, 'Проверьте заявку на главной')
        self.assertEqual(len(mail.outbox), 1)

    def test_invalid_feedback_is_not_success(self):
        response = self.client.post(
            reverse('feedback'),
            {'name': '', 'phone': '', 'message': ''},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])
        self.assertEqual(Feedback.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        OPENAI_API_KEY='sk-test-guide-no-auto-email',
        HELP_EMAIL_ENABLED=True,
        HELP_NOTIFICATION_EMAIL='help-admin@test.local',
        FEEDBACK_NOTIFY_EMAIL='feedback@test.local',
    )
    def test_guide_ask_does_not_email_until_explicit_feedback(self):
        fake = {'output_text': 'Откройте форму заявки на главной.'}

        def fake_post(url, **kwargs):
            return type('Resp', (), {
                'status_code': 200,
                'text': json.dumps(fake),
                'json': lambda self=None: fake,
            })()

        with patch('core.platform_help.requests.post', fake_post):
            ask = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'Как оставить запрос на запчасть?'}),
                content_type='application/json',
            )
            ask_again = self.client.post(
                reverse('platform_help_ask'),
                data=json.dumps({'message': 'А как добавить на главный экран?'}),
                content_type='application/json',
            )
        self.assertEqual(ask.status_code, 200)
        self.assertEqual(ask_again.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Feedback.objects.count(), 0)

        feedback = self.client.post(
            reverse('feedback'),
            {
                'name': 'Тест',
                'phone': '77011234567',
                'message': 'Как оставить запрос на запчасть?',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(feedback.status_code, 200)
        self.assertTrue(feedback.json()['success'])
        self.assertEqual(Feedback.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)


class HomeShowcaseLimitTests(TestCase):
    def setUp(self):
        self.client = Client()
        user = User.objects.create_user('showcase-limit', password='secret12345')
        seller = SellerProfile.objects.create(
            user=user,
            name='Limit Seller',
            phone='77019990001',
            city='Алматы',
        )
        for index in range(10):
            Product.objects.create(
                title=f'Витринный товар {index}',
                slug=f'showcase-limit-{index}',
                article=f'SHOW-{index}',
                price=1000 + index,
                seller_name=seller.name,
                whatsapp_number=seller.phone,
                status='active',
                city='Алматы',
            )

    def test_home_renders_eight_cards_catalog_keeps_more(self):
        home = self.client.get(reverse('catalog_list'))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, 'class="products home-showcase-products"')
        self.assertEqual(home.content.decode().count('class="product product-v2"'), 8)

        listing = self.client.get(reverse('catalog_list'), {'all': '1'})
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, 'class="products home-showcase-products"')
        self.assertGreaterEqual(listing.content.decode().count('class="product product-v2"'), 10)
        self.assertContains(listing, 'Популярные марки')
        self.assertNotContains(home, 'Популярные марки')
        self.assertNotContains(home, 'Распродажа')
        self.assertNotContains(home, 'Автозапчасти для автомобилей в Казахстане')


class HomePwaPromptTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _home_request(self, **kwargs):
        defaults = {
            'phone': '77015556677',
            'description': 'тормозные колодки',
            'city': 'Алматы',
            'source': Request.SOURCE_HOME_SHORT,
            'status': 'no_sellers',
        }
        defaults.update(kwargs)
        return Request.objects.create(**defaults)

    def test_plain_home_does_not_include_pwa_prompt(self):
        response = self.client.get(reverse('catalog_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-zpt-pwa="home-success"')
        self.assertNotContains(response, 'Добавьте ZPT на главный экран')

    def test_success_result_page_includes_pwa_prompt(self):
        req = self._home_request()
        response = self.client.get(reverse('catalog_list'), {'home_request': str(req.access_token)})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-zpt-pwa="home-success"', html)
        self.assertIn('Добавьте ZPT на главный экран', html)
        self.assertIn('data-zpt-pwa-dismiss', html)
        self.assertIn('Позже', html)
        self.assertContains(response, 'data-zpt-home-result="1"')

    def test_invalid_or_classic_token_does_not_include_pwa_prompt(self):
        invalid = self.client.get(reverse('catalog_list'), {'home_request': 'not-a-uuid'})
        self.assertEqual(invalid.status_code, 200)
        self.assertNotContains(invalid, 'data-zpt-pwa="home-success"')

        classic = self._home_request(source=Request.SOURCE_CLASSIC)
        classic_page = self.client.get(
            reverse('catalog_list'),
            {'home_request': str(classic.access_token)},
        )
        self.assertEqual(classic_page.status_code, 200)
        self.assertNotContains(classic_page, 'data-zpt-pwa="home-success"')

    @override_settings(
        PUBLIC_BASE_URL='https://zpt.kz',
        HOME_PARTS_MAX_PER_HOUR=0,
        HOME_PARTS_MAX_PER_PHONE_HOUR=0,
    )
    def test_api_success_result_url_has_prompt_and_error_does_not(self):
        with patch('core.views._send_buyer_whatsapp_notification_async') as buyer_wa, patch(
            'core.views.schedule_instagram_publication_for_request',
        ):
            success = self.client.post(
                '/api/home-parts-request/',
                {
                    'query': 'тормозные колодки',
                    'brand': 'Toyota',
                    'model': 'Camry',
                    'city': 'Алматы',
                    'phone': '87015556677',
                    'idempotency_key': 'pwa-success-key',
                },
            )
            error = self.client.post(
                '/api/home-parts-request/',
                {
                    'query': '',
                    'phone': 'bad',
                    'idempotency_key': 'pwa-error-key',
                },
            )
        self.assertEqual(success.status_code, 200, success.content)
        payload = success.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertTrue(payload['result_url'].startswith('/?home_request='))
        buyer_wa.assert_called_once()
        result = self.client.get(payload['result_url'])
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, 'data-zpt-pwa="home-success"')

        self.assertNotEqual(error.status_code, 200)
        self.assertNotIn('result_url', error.json())
        home = self.client.get(reverse('catalog_list'))
        self.assertNotContains(home, 'data-zpt-pwa="home-success"')
