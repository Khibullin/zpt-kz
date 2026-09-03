from __future__ import annotations

from datetime import datetime

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from marketing.models import MarketingCampaignSendRun
from marketing.services.campaigns.constants import (
    CHANNEL_WHATSAPP,
    STATUS_AUDIENCE_PREPARED,
)
from marketing.services.campaigns.send_constants import (
    SEND_MODE_LIVE,
    SEND_RUN_STATUS_COMPLETED,
    WORKFLOW_TYPE_SIMPLE_MAILING,
)
from marketing.tests.test_marketing_audiences import grant_marketing_permission
from marketing.tests.test_marketing_campaign_send import make_test_send_template
from marketing.tests.test_marketing_campaigns import make_audience, make_campaign


class MarketingHistoryPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('history', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client = Client()
        self.client.force_login(self.user)
        self.template = make_test_send_template(self.user)
        self.long_campaign_name = (
            'Покупатели по заявкам на запчасти — Genesis, Haval, Hino, Honda, '
            'Hongqi, Hyundai, Infiniti, Iveco, JAC, Jaecoo, Jeep, Jetour, Kia, '
            'Lada, Lexus — 31.07.2026'
        )
        self.created_at = timezone.make_aware(datetime(2026, 7, 31, 8, 41))
        self.campaign = make_campaign(
            make_audience(name='History audience', created_by=self.user),
            self.user,
            name=self.long_campaign_name,
            channel=CHANNEL_WHATSAPP,
            status=STATUS_AUDIENCE_PREPARED,
            message_template=self.template,
        )
        self.send_run = MarketingCampaignSendRun.objects.create(
            campaign=self.campaign,
            template=self.template,
            mode=SEND_MODE_LIVE,
            status=SEND_RUN_STATUS_COMPLETED,
            workflow_type=WORKFLOW_TYPE_SIMPLE_MAILING,
            total_count=50,
            sent_count=46,
            skipped_count=4,
            failed_count=0,
            created_by=self.user,
        )
        MarketingCampaignSendRun.objects.filter(pk=self.send_run.pk).update(
            created_at=self.created_at,
        )
        self.send_run.refresh_from_db()

    def test_history_page_shows_compact_campaign_heading(self):
        response = self.client.get(reverse('marketing:history'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Кампания №{self.campaign.pk}')
        self.assertContains(
            response,
            timezone.localtime(self.send_run.created_at).strftime('%d.%m.%Y %H:%M'),
        )
        self.assertContains(response, self.long_campaign_name)
        self.assertContains(
            response,
            reverse('marketing:campaign_detail', args=[self.campaign.pk]),
        )
        self.assertContains(response, 'marketing-history-run__campaign-name')
        self.assertContains(response, 'marketing-history-run__heading')
        self.assertContains(response, 'LIVE')
