from datetime import timedelta
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase
from django.utils import timezone

from core.services.request_attribution import (
    SESSION_KEY, capture_request_campaign, get_request_campaign,
)


class RequestCampaignTests(SimpleTestCase):
    def request(self, query=''):
        request = RequestFactory().get('/find-part/' + query)
        request.session = {}
        return request

    def test_capture_and_direct_return_preserve_campaign(self):
        request = self.request('?utm_source=telegram&utm_campaign=auto_news_kz')
        capture_request_campaign(request)
        request.GET = {}
        capture_request_campaign(request)
        self.assertEqual(get_request_campaign(request)['utm_campaign'], 'auto_news_kz')

    def test_new_source_does_not_inherit_previous_campaign(self):
        request = self.request('?utm_source=telegram&utm_campaign=old')
        capture_request_campaign(request)
        request.GET = {'utm_source': 'other'}
        capture_request_campaign(request)
        self.assertEqual(get_request_campaign(request)['utm_campaign'], '')

    def test_expired_campaign_is_not_attributed(self):
        request = self.request('?utm_source=telegram')
        capture_request_campaign(request)
        with patch('core.services.request_attribution.timezone.now',
                   return_value=timezone.now() + timedelta(days=8)):
            self.assertEqual(get_request_campaign(request), {})

    def test_untrusted_values_are_bounded_and_control_characters_removed(self):
        request = self.request()
        request.GET = {'utm_source': 'a' * 500, 'utm_campaign': 'test\n\r\tpost'}
        capture_request_campaign(request)
        self.assertEqual(len(get_request_campaign(request)['utm_source']), 100)
        self.assertEqual(get_request_campaign(request)['utm_campaign'], 'testpost')

    def test_malformed_or_future_timestamp_is_ignored(self):
        request = self.request()
        for stamp in ('bad', float('nan'), float('inf'), timezone.now().timestamp() + 60):
            request.session[SESSION_KEY] = {'utm_source': 'telegram', 'captured_at': stamp}
            self.assertEqual(get_request_campaign(request), {})
