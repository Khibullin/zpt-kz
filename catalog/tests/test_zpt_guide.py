"""ZPT Гид page, redirects, FAQ source and homepage showcase limit."""
from __future__ import annotations

import json
from unittest.mock import patch

from pathlib import Path

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, SellerProfile
from core.models import Feedback, GuideFaqVote, Request
from core.platform_help import platform_help_system_prompt
from core.zpt_guide_faq import (
    FAQ_SECTIONS,
    PUBLIC_TOPICS,
    TOP10_IDS,
    faq_item_count,
    faq_prompt_block,
    top10_items,
)


class ZptGuideFaqSourceTests(TestCase):
    def test_faq_has_unique_questions_and_shared_source(self):
        self.assertEqual(faq_item_count(), 31)
        questions = [item['question'] for _, item in _iter_items()]
        self.assertEqual(len(set(questions)), 31)
        prompt = faq_prompt_block()
        self.assertIn('ZPT Гид', prompt)
        self.assertNotIn('GPT Guide', prompt)
        self.assertNotIn('GPT Гид', prompt)
        combined = platform_help_system_prompt()
        self.assertIn('Как оставить заявку на запчасть?', combined)
        self.assertIn('Что входит в комплект и входит ли масло?', combined)
        self.assertIn(prompt, combined)

    def test_kit_answers_stay_in_source_but_have_no_public_topic(self):
        answers = ' '.join(item['answer'] for _, item in _iter_items())
        self.assertIn('Масло не входит во все наборы', answers)
        self.assertIn('Свечи входят не во все комплекты', answers)
        titles = [topic['title'] for topic in PUBLIC_TOPICS]
        self.assertEqual(
            titles,
            [
                'Поиск запчастей и заявка',
                'Предложения продавцов',
                'Покупка, оплата и доставка',
                'Возврат и гарантия',
                'Оптовые покупки',
                'Продавцам',
            ],
        )
        all_topic_ids = {faq_id for topic in PUBLIC_TOPICS for faq_id in topic['item_ids']}
        self.assertNotIn('q14', all_topic_ids)
        self.assertNotIn('q15', all_topic_ids)

    def test_top10_uses_canonical_records(self):
        items = top10_items()
        self.assertEqual([item['id'] for item in items], list(TOP10_IDS))
        self.assertEqual(items[0]['question'], 'Как оставить заявку на запчасть?')
        self.assertEqual(items[1]['question'], 'Что делать, если я не знаю артикул?')
        self.assertEqual(items[2]['question'], 'Можно ли отправить VIN или фото запчасти?')
        self.assertEqual(items[3]['question'], 'Куда придут предложения продавцов?')
        self.assertEqual(items[4]['question'], 'Что делать, если продавцы не ответили?')
        self.assertEqual(items[5]['question'], 'Как уточнить наличие и цену?')
        self.assertEqual(items[6]['question'], 'Как проверить, подойдёт ли запчасть?')
        self.assertEqual(items[7]['question'], 'Как договориться об оплате и доставке?')
        self.assertEqual(items[8]['question'], 'Как обратиться по возврату или гарантии?')
        self.assertEqual(items[9]['question'], 'Как связаться со специалистом ZPT?')
        for item in items:
            self.assertTrue(item['updated_at'])
            self.assertNotEqual(item['updated_at'].strftime('%d.%m.%Y'), '01.01.1970')


def _iter_items():
    for section in FAQ_SECTIONS:
        for item in section['items']:
            yield section, item


class ZptGuidePageTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_guide_page_renders_faq_first_and_separate_sections(self):
        response = self.client.get(reverse('zpt_gid'))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ZPT Гид')
        self.assertContains(response, 'Частые вопросы')
        self.assertContains(response, 'Обратная связь')
        self.assertContains(response, 'ИИ-помощник')
        self.assertContains(response, 'Топ 10 вопросов')
        self.assertContains(response, 'Поиск по вопросам')
        self.assertContains(response, 'Написать специалисту')
        self.assertContains(response, 'Напишите нам — специалист ответит по указанному контакту.')
        self.assertContains(response, 'Телефон / WhatsApp')
        self.assertContains(response, '+7 701 123 45 67')
        self.assertContains(response, 'aria-label="Отправить"')
        self.assertNotContains(response, 'Обращение получит человек. Мгновенный ответ не обещаем.')
        self.assertGreaterEqual(html.count('aria-expanded="false"'), 10)
        self.assertNotIn('aria-expanded="true"', html)
        self.assertIn('id="faq-answer-q1" hidden', html)
        self.assertContains(response, 'Отправить обращение')
        self.assertContains(response, 'id="help-input"')
        self.assertContains(response, 'Напишите вопрос…')
        self.assertContains(response, 'Новый диалог')
        self.assertContains(response, 'static/js/platform-help-v2.js')
        self.assertContains(response, 'static/js/zpt-guide-v2.js')
        self.assertContains(response, 'id="spravka"')
        self.assertContains(response, 'id="svyaz"')
        self.assertContains(response, 'id="pomoshchnik"')
        self.assertContains(response, 'id="na-ekran"')
        self.assertContains(response, 'Добавить иконку ZPT')
        self.assertContains(response, 'Как оставить заявку на запчасть?')
        self.assertContains(response, 'Поиск запчастей и заявка')
        self.assertNotContains(response, 'Чем помочь?')
        self.assertNotContains(response, 'Справка')
        self.assertNotContains(response, 'Поиск по вопросам и ответам ZPT Гида')
        self.assertNotContains(response, 'Найти в справке')
        self.assertNotContains(response, 'Задать вопрос голосом')
        self.assertNotContains(response, 'id="help-whatsapp"')
        self.assertNotContains(response, '72%')
        self.assertNotContains(response, 'GPT Guide')
        self.assertNotContains(response, 'GPT Гид')
        self.assertNotContains(response, 'Что входит в комплект и входит ли масло?')
        self.assertIn('csrftoken', response.cookies)
        self.assertEqual(html.count('data-faq-id="q1"'), 1)
        faq_pos = html.find('id="spravka"')
        feedback_pos = html.find('id="svyaz"')
        self.assertGreater(faq_pos, 0)
        self.assertGreater(feedback_pos, faq_pos)
        self.assertIn('hidden', html[feedback_pos:feedback_pos + 80])

    def test_helper_dialog_copy_is_short(self):
        response = self.client.get(reverse('zpt_gid'))
        self.assertContains(response, 'Помогу разобраться с сайтом и найти запчасть в каталоге')
        self.assertContains(response, 'Ответ даёт ИИ и появляется в этом окне.')
        js = Path('static/js/platform-help-v2.js').read_text(encoding='utf-8')
        self.assertNotIn('Я помощник ZPT.KZ', js)
        self.assertNotIn('Задать вопрос голосом', js)
        self.assertIn('zptGuideRequestDraft', js)
        self.assertIn('window.location.href = \'/\';', js)
        home_js = Path('static/js/home-parts-form-v3.js').read_text(encoding='utf-8')
        self.assertIn("fillIfEmpty(vinEl, draft.vin)", home_js)
        self.assertIn('fillIfEmpty', home_js)
        self.assertNotIn('draft.phone', home_js)

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


class ZptGuideFaqVoteTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.get(reverse('zpt_gid'))

    def test_first_vote_saves_and_repeat_updates_same_row(self):
        url = reverse('zpt_guide_faq_vote')
        first = self.client.post(
            url,
            data=json.dumps({'faq_id': 'q1', 'helpful': True}),
            content_type='application/json',
        )
        self.assertEqual(first.status_code, 200, first.content)
        payload = first.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['helpful_percent'], 100)
        self.assertTrue(payload['my_vote'])
        self.assertEqual(GuideFaqVote.objects.count(), 1)

        second = self.client.post(
            url,
            data=json.dumps({'faq_id': 'q1', 'helpful': False}),
            content_type='application/json',
        )
        self.assertEqual(second.status_code, 200)
        updated = second.json()
        self.assertEqual(GuideFaqVote.objects.count(), 1)
        self.assertEqual(updated['total'], 1)
        self.assertEqual(updated['helpful_percent'], 0)
        self.assertFalse(updated['my_vote'])
        vote = GuideFaqVote.objects.get()
        self.assertFalse(vote.helpful)

    def test_percent_hidden_until_votes_and_invalid_is_not_success(self):
        page = self.client.get(reverse('zpt_gid'))
        self.assertNotContains(page, 'считают ответ полезным')
        self.assertNotContains(page, '72%')
        bad = self.client.post(
            reverse('zpt_guide_faq_vote'),
            data=json.dumps({'faq_id': 'missing', 'helpful': True}),
            content_type='application/json',
        )
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.json()['ok'])
        self.assertEqual(GuideFaqVote.objects.count(), 0)

    def test_other_session_does_not_reuse_vote(self):
        self.client.post(
            reverse('zpt_guide_faq_vote'),
            data=json.dumps({'faq_id': 'q7', 'helpful': True}),
            content_type='application/json',
        )
        other = Client()
        other.get(reverse('zpt_gid'))
        other.post(
            reverse('zpt_guide_faq_vote'),
            data=json.dumps({'faq_id': 'q7', 'helpful': False}),
            content_type='application/json',
        )
        self.assertEqual(GuideFaqVote.objects.count(), 2)
        stats = other.post(
            reverse('zpt_guide_faq_vote'),
            data=json.dumps({'faq_id': 'q7', 'helpful': False}),
            content_type='application/json',
        ).json()
        self.assertEqual(stats['total'], 2)
        self.assertEqual(stats['helpful_percent'], 50)


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
