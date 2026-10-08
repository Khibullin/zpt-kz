from __future__ import annotations

import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from catalog.models import SellerProfile
from catalog.product_article_recognition import (
    ArticleRecognition,
    recognize_article_from_upload,
)


PASSWORD = 'StrongSellerPass123!'


def _image_upload(name='label.png'):
    buffer = io.BytesIO()
    Image.new('RGB', (320, 180), 'white').save(buffer, format='PNG')
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type='image/png',
    )


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode('utf-8')


class ProductArticleRecognitionServiceTests(TestCase):
    @override_settings(OPENAI_API_KEY='test-key', PRODUCT_AI_MODEL='gpt-5.6-luna')
    def test_single_visible_article_is_returned(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured['body'] = json.loads(request.data.decode('utf-8'))
            captured['timeout'] = timeout
            return _FakeResponse({
                'output_text': json.dumps({
                    'article': 'M11-1109111',
                    'candidates': ['M11-1109111'],
                    'confidence': 'confirmed',
                }),
            })

        result = recognize_article_from_upload(
            _image_upload(),
            urlopen=fake_urlopen,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.article, 'M11-1109111')
        self.assertEqual(result.candidates, ['M11-1109111'])
        self.assertEqual(result.confidence, 'confirmed')
        content = captured['body']['input'][0]['content']
        self.assertEqual(content[0]['type'], 'input_text')
        self.assertEqual(content[1]['type'], 'input_image')
        self.assertTrue(content[1]['image_url'].startswith('data:image/webp;base64,'))
        self.assertEqual(content[1]['detail'], 'high')

    @override_settings(OPENAI_API_KEY='test-key')
    def test_multiple_candidates_never_auto_selects(self):
        result = recognize_article_from_upload(
            _image_upload(),
            urlopen=lambda request, timeout: _FakeResponse({
                'output_text': json.dumps({
                    'article': 'A12345',
                    'candidates': ['A12345', 'A12346'],
                    'confidence': 'likely',
                }),
            }),
        )

        self.assertEqual(result.article, '')
        self.assertEqual(result.candidates, ['A12345', 'A12346'])
        self.assertEqual(result.confidence, 'needs_verification')

    @override_settings(OPENAI_API_KEY='')
    def test_missing_api_key_returns_none_after_validating_photo(self):
        result = recognize_article_from_upload(_image_upload())
        self.assertIsNone(result)

    def test_invalid_image_is_rejected(self):
        upload = SimpleUploadedFile(
            'bad.jpg',
            b'not-an-image',
            content_type='image/jpeg',
        )
        with self.assertRaises(ValidationError):
            recognize_article_from_upload(upload)


class ProductArticleRecognitionViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username='77015550201',
            password=PASSWORD,
        )
        SellerProfile.objects.create(
            user=user,
            name='Vision Seller',
            phone='77015550201',
            city='Алматы',
        )
        self.user = user

    def test_endpoint_requires_login(self):
        response = self.client.post(
            reverse('ajax_product_article_recognition'),
            {'image': _image_upload()},
        )
        self.assertEqual(response.status_code, 302)

    @patch('catalog.views.recognize_article_from_upload')
    def test_endpoint_returns_recognized_article(self, mocked):
        mocked.return_value = ArticleRecognition(
            article='26300-35505',
            candidates=['26300-35505'],
            confidence='confirmed',
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('ajax_product_article_recognition'),
            {'image': _image_upload()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['article'], '26300-35505')
        self.assertTrue(response.json()['ok'])

    @patch('catalog.views.recognize_article_from_upload')
    def test_endpoint_reports_multiple_candidates_without_guessing(self, mocked):
        mocked.return_value = ArticleRecognition(
            article='',
            candidates=['A12345', 'A12346'],
            confidence='needs_verification',
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('ajax_product_article_recognition'),
            {'image': _image_upload()},
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(response.json()['ok'])
        self.assertEqual(response.json()['candidates'], ['A12345', 'A12346'])

    def test_add_product_page_contains_photo_recognition_control(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('add_product'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Распознать с фото')
        self.assertContains(response, 'data-article-recognition-url=')
        self.assertContains(response, 'capture="environment"')
