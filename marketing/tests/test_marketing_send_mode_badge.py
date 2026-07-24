from __future__ import annotations

import os

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from marketing.services.campaigns.send_settings import get_marketing_whatsapp_send_mode
from marketing.tests.test_marketing_audiences import grant_marketing_permission


class MarketingSendModeBadgeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='badge_admin',
            password='pass12345',
            email='badge@example.com',
        )
        self.client.login(username='badge_admin', password='pass12345')
        self.dashboard_url = '/marketing/'

    def _fetch_badge_text(self) -> str:
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    @override_settings(MARKETING_WHATSAPP_SEND_MODE='OFF', BUYER_BROADCAST_MODE='TEST')
    def test_env_off_shows_off_even_if_broadcast_test(self):
        content = self._fetch_badge_text()
        self.assertIn('Режим: OFF', content)
        self.assertNotIn('Режим: TEST', content)
        self.assertEqual(get_marketing_whatsapp_send_mode(), 'OFF')

    @override_settings(MARKETING_WHATSAPP_SEND_MODE='TEST')
    def test_env_test_shows_test(self):
        self.assertIn('Режим: TEST', self._fetch_badge_text())
        self.assertEqual(get_marketing_whatsapp_send_mode(), 'TEST')

    @override_settings(MARKETING_WHATSAPP_SEND_MODE='LIVE')
    def test_env_live_shows_live(self):
        content = self._fetch_badge_text()
        self.assertIn('Режим: LIVE', content)
        self.assertIn('marketing-mode-badge--live', content)
        self.assertEqual(get_marketing_whatsapp_send_mode(), 'LIVE')

    @override_settings(MARKETING_WHATSAPP_SEND_MODE='OFF')
    def test_env_absent_defaults_to_off(self):
        old_value = os.environ.pop('MARKETING_WHATSAPP_SEND_MODE', None)
        try:
            self.assertEqual(get_marketing_whatsapp_send_mode(), 'OFF')
            self.assertIn('Режим: OFF', self._fetch_badge_text())
        finally:
            if old_value is not None:
                os.environ['MARKETING_WHATSAPP_SEND_MODE'] = old_value

    @override_settings(MARKETING_WHATSAPP_SEND_MODE='OFF')
    def test_env_invalid_defaults_to_off(self):
        os.environ['MARKETING_WHATSAPP_SEND_MODE'] = 'BROKEN'
        try:
            self.assertEqual(get_marketing_whatsapp_send_mode(), 'OFF')
            self.assertIn('Режим: OFF', self._fetch_badge_text())
        finally:
            os.environ.pop('MARKETING_WHATSAPP_SEND_MODE', None)

    @override_settings(MARKETING_WHATSAPP_SEND_MODE='TEST')
    def test_ui_matches_backend_resolved_mode(self):
        mode = get_marketing_whatsapp_send_mode()
        content = self._fetch_badge_text()
        self.assertIn(f'Режим: {mode}', content)

    @override_settings(MARKETING_WHATSAPP_SEND_MODE='OFF')
    def test_buyer_vehicles_page_uses_marketing_send_mode(self):
        grant_marketing_permission(self.user)
        response = self.client.get('/marketing/buyer-vehicles/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Режим: OFF')
