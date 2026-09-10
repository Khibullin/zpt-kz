import os
from unittest.mock import patch

from django.test import TestCase


class GoogleTagManagerTemplateTests(TestCase):
    def test_gtm_is_absent_when_id_is_not_configured(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('GOOGLE_TAG_MANAGER_ID', None)
            home = self.client.get('/')
            request_parts = self.client.get('/request-parts/')

        self.assertNotContains(home, 'googletagmanager.com')
        self.assertNotContains(request_parts, 'googletagmanager.com')

    def test_invalid_gtm_id_is_ignored(self):
        with patch.dict(os.environ, {'GOOGLE_TAG_MANAGER_ID': 'G-INVALID'}, clear=False):
            response = self.client.get('/')

        self.assertNotContains(response, 'googletagmanager.com')

    def test_valid_gtm_id_is_rendered_in_main_and_portal_bases(self):
        with patch.dict(os.environ, {'GOOGLE_TAG_MANAGER_ID': 'GTM-ABC123'}, clear=False):
            home = self.client.get('/')
            request_parts = self.client.get('/request-parts/')

        for response in (home, request_parts):
            self.assertContains(response, 'https://www.googletagmanager.com/gtm.js?id=')
            self.assertContains(response, 'GTM-ABC123')
            self.assertContains(response, 'https://www.googletagmanager.com/ns.html?id=GTM-ABC123')
