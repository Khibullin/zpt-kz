from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from catalog.instagram_api import (
    InstagramPublishError,
    build_public_media_url,
    normalize_media_relative_path,
    publish_story_to_instagram,
    try_publish_story_to_instagram,
    validate_public_image_url,
    _sanitize_for_log,
)

VALID_JPEG_BODY = b'\xff\xd8\xff' + b'fake-jpeg-body'
IMAGE_RELATIVE_PATH = 'instagram_stories/request_1_20260706_150000.jpg'
PUBLIC_IMAGE_URL = (
    'https://zpt.kz/products/instagram_stories/request_1_20260706_150000.jpg'
)


def _valid_image_response(*, status_code=200, content_type='image/jpeg', body=VALID_JPEG_BODY):
    response = MagicMock()
    response.status_code = status_code
    response.url = PUBLIC_IMAGE_URL
    response.headers = {
        'Content-Type': content_type,
        'Content-Length': str(len(body)),
    }
    response.content = body
    return response


@override_settings(
    PUBLIC_BASE_URL='https://zpt.kz',
    MEDIA_URL='/products/',
    INSTAGRAM_BUSINESS_ACCOUNT_ID='17841400000000000',
    FACEBOOK_ACCESS_TOKEN='test-token',
    META_GRAPH_API_VERSION='v20.0',
)
class InstagramApiTests(TestCase):
    def test_build_public_media_url(self):
        url = build_public_media_url(IMAGE_RELATIVE_PATH)
        self.assertEqual(url, PUBLIC_IMAGE_URL)
        self.assertTrue(url.startswith('https://'))

    def test_normalize_media_relative_path_rejects_admin_path(self):
        with self.assertRaises(InstagramPublishError):
            normalize_media_relative_path('admin/login/?next=/products/file.jpg')

    def test_normalize_media_relative_path_rejects_absolute_url(self):
        with self.assertRaises(InstagramPublishError):
            normalize_media_relative_path(PUBLIC_IMAGE_URL)

    @patch('catalog.instagram_api.requests.get')
    def test_validate_public_image_url_accepts_jpeg(self, get_mock):
        get_mock.return_value = _valid_image_response()

        result = validate_public_image_url(PUBLIC_IMAGE_URL)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content_type, 'image/jpeg')
        get_mock.assert_called_once_with(
            PUBLIC_IMAGE_URL,
            timeout=30,
            allow_redirects=True,
            headers={'User-Agent': 'ZPT.KZ-Instagram-Validator/1.0'},
        )

    def test_sanitize_for_log_redacts_access_token(self):
        payload = {
            'id': 'container_123',
            'access_token': 'secret-token',
            'nested': {'token': 'nested-secret'},
        }
        sanitized = _sanitize_for_log(payload)
        self.assertEqual(sanitized['access_token'], '***')
        self.assertEqual(sanitized['nested']['token'], '***')
        self.assertEqual(sanitized['id'], 'container_123')

    @patch('catalog.instagram_api.requests.get')
    def test_validate_public_image_url_rejects_html(self, get_mock):
        get_mock.return_value = _valid_image_response(
            content_type='text/html; charset=utf-8',
            body=b'<!doctype html><html><body>Login</body></html>',
        )

        with self.assertRaises(InstagramPublishError) as ctx:
            validate_public_image_url(PUBLIC_IMAGE_URL)

        self.assertIn('text/html', str(ctx.exception))

    @patch('catalog.instagram_api.requests.get')
    def test_validate_public_image_url_rejects_404(self, get_mock):
        get_mock.return_value = _valid_image_response(status_code=404)

        with self.assertRaises(InstagramPublishError) as ctx:
            validate_public_image_url(PUBLIC_IMAGE_URL)

        self.assertIn('404', str(ctx.exception))

    @override_settings(INSTAGRAM_BUSINESS_ACCOUNT_ID='', FACEBOOK_ACCESS_TOKEN='')
    def test_try_publish_skips_without_credentials(self):
        result = try_publish_story_to_instagram(IMAGE_RELATIVE_PATH)
        self.assertIsNone(result)

    @patch('catalog.instagram_api._wait_for_container_ready')
    @patch('catalog.instagram_api.requests.get')
    @patch('catalog.instagram_api.requests.post')
    def test_publish_story_to_instagram_success(self, post_mock, get_mock, wait_mock):
        get_mock.return_value = _valid_image_response()

        create_response = MagicMock()
        create_response.status_code = 200
        create_response.json.return_value = {'id': 'container_123'}

        publish_response = MagicMock()
        publish_response.status_code = 200
        publish_response.json.return_value = {'id': 'media_456'}

        post_mock.side_effect = [create_response, publish_response]

        media_id = publish_story_to_instagram(IMAGE_RELATIVE_PATH)

        self.assertEqual(media_id['media_id'], 'media_456')
        self.assertEqual(media_id['container_id'], 'container_123')
        self.assertEqual(post_mock.call_count, 2)
        get_mock.assert_called_once()

        create_call = post_mock.call_args_list[0]
        self.assertIn('/17841400000000000/media', create_call.args[0])
        self.assertEqual(create_call.kwargs['data']['media_type'], 'STORIES')
        self.assertEqual(create_call.kwargs['data']['image_url'], PUBLIC_IMAGE_URL)

        publish_call = post_mock.call_args_list[1]
        self.assertIn('/17841400000000000/media_publish', publish_call.args[0])
        self.assertEqual(publish_call.kwargs['data']['creation_id'], 'container_123')
        wait_mock.assert_called_once_with(
            container_id='container_123',
            access_token='test-token',
            publication_id=None,
        )

    @patch('catalog.instagram_api.requests.get')
    @patch('catalog.instagram_api.requests.post')
    def test_publish_story_does_not_call_meta_when_image_url_invalid(self, post_mock, get_mock):
        get_mock.return_value = _valid_image_response(status_code=404)

        with self.assertRaises(InstagramPublishError):
            publish_story_to_instagram(IMAGE_RELATIVE_PATH)

        post_mock.assert_not_called()

    @patch('catalog.instagram_api.requests.get')
    @patch('catalog.instagram_api.requests.post')
    def test_publish_story_does_not_call_meta_when_html_returned(self, post_mock, get_mock):
        get_mock.return_value = _valid_image_response(
            content_type='text/html; charset=utf-8',
            body=b'<!doctype html><html><body>Login</body></html>',
        )

        with self.assertRaises(InstagramPublishError):
            publish_story_to_instagram(IMAGE_RELATIVE_PATH)

        post_mock.assert_not_called()

    @patch('catalog.instagram_api.requests.post')
    def test_publish_story_raises_on_graph_error(self, post_mock):
        with patch('catalog.instagram_api.requests.get', return_value=_valid_image_response()):
            error_response = MagicMock()
            error_response.status_code = 400
            error_response.text = 'bad request'
            error_response.json.return_value = {
                'error': {'message': 'Invalid OAuth access token', 'code': 190},
            }
            post_mock.return_value = error_response

            with self.assertRaises(InstagramPublishError):
                publish_story_to_instagram(IMAGE_RELATIVE_PATH)

    @patch('catalog.instagram_api.publish_story_to_instagram')
    def test_try_publish_swallows_publish_error(self, publish_mock):
        publish_mock.side_effect = InstagramPublishError('token invalid')
        result = try_publish_story_to_instagram(IMAGE_RELATIVE_PATH)
        self.assertIsNone(result)

    @patch('catalog.instagram_api.publish_story_to_instagram')
    def test_try_publish_swallows_network_error(self, publish_mock):
        import requests

        publish_mock.side_effect = requests.Timeout('timeout')
        result = try_publish_story_to_instagram(IMAGE_RELATIVE_PATH)
        self.assertIsNone(result)
