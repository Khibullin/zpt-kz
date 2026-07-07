from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from catalog.instagram_api import (
    InstagramPublishError,
    build_public_media_url,
    publish_story_to_instagram,
    try_publish_story_to_instagram,
)


@override_settings(
    PUBLIC_BASE_URL='https://zpt.kz',
    MEDIA_URL='/products/',
    INSTAGRAM_BUSINESS_ACCOUNT_ID='17841400000000000',
    FACEBOOK_ACCESS_TOKEN='test-token',
    META_GRAPH_API_VERSION='v20.0',
)
class InstagramApiTests(TestCase):
    def test_build_public_media_url(self):
        url = build_public_media_url('instagram_stories/request_1.png')
        self.assertEqual(
            url,
            'https://zpt.kz/products/instagram_stories/request_1.png',
        )

    @override_settings(INSTAGRAM_BUSINESS_ACCOUNT_ID='', FACEBOOK_ACCESS_TOKEN='')
    def test_try_publish_skips_without_credentials(self):
        result = try_publish_story_to_instagram('instagram_stories/request_1.png')
        self.assertIsNone(result)

    @patch('catalog.instagram_api._wait_for_container_ready')
    @patch('catalog.instagram_api.requests.post')
    def test_publish_story_to_instagram_success(self, post_mock, wait_mock):
        create_response = MagicMock()
        create_response.status_code = 200
        create_response.json.return_value = {'id': 'container_123'}

        publish_response = MagicMock()
        publish_response.status_code = 200
        publish_response.json.return_value = {'id': 'media_456'}

        post_mock.side_effect = [create_response, publish_response]

        media_id = publish_story_to_instagram('instagram_stories/request_1.png')

        self.assertEqual(media_id['media_id'], 'media_456')
        self.assertEqual(media_id['container_id'], 'container_123')
        self.assertEqual(post_mock.call_count, 2)

        create_call = post_mock.call_args_list[0]
        self.assertIn('/17841400000000000/media', create_call.args[0])
        self.assertEqual(create_call.kwargs['data']['media_type'], 'STORIES')
        self.assertEqual(
            create_call.kwargs['data']['image_url'],
            'https://zpt.kz/products/instagram_stories/request_1.png',
        )

        publish_call = post_mock.call_args_list[1]
        self.assertIn('/17841400000000000/media_publish', publish_call.args[0])
        self.assertEqual(publish_call.kwargs['data']['creation_id'], 'container_123')
        wait_mock.assert_called_once_with(
            container_id='container_123',
            access_token='test-token',
        )

    @patch('catalog.instagram_api.requests.post')
    def test_publish_story_raises_on_graph_error(self, post_mock):
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.text = 'bad request'
        error_response.json.return_value = {
            'error': {'message': 'Invalid OAuth access token', 'code': 190},
        }
        post_mock.return_value = error_response

        with self.assertRaises(InstagramPublishError):
            publish_story_to_instagram('instagram_stories/request_1.png')

    @patch('catalog.instagram_api.publish_story_to_instagram')
    def test_try_publish_swallows_publish_error(self, publish_mock):
        publish_mock.side_effect = InstagramPublishError('token invalid')
        result = try_publish_story_to_instagram('instagram_stories/request_1.png')
        self.assertIsNone(result)

    @patch('catalog.instagram_api.publish_story_to_instagram')
    def test_try_publish_swallows_network_error(self, publish_mock):
        import requests

        publish_mock.side_effect = requests.Timeout('timeout')
        result = try_publish_story_to_instagram('instagram_stories/request_1.png')
        self.assertIsNone(result)
