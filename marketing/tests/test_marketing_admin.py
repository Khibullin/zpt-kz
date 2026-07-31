from __future__ import annotations

from datetime import datetime

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.admin import WhatsAppMessageLogAdmin
from core.models import WhatsAppMessageLog
from marketing.admin import (
    MarketingCampaignMessageAdmin,
    MarketingCampaignSendRunAdmin,
    MarketingWhatsAppTemplateAdmin,
)
from marketing.models import (
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignRecipient,
    MarketingCampaignSendRun,
    MarketingWhatsAppTemplate,
)
from marketing.services.campaigns.constants import (
    CHANNEL_WHATSAPP,
    ELIGIBILITY_ELIGIBLE,
    STATUS_AUDIENCE_PREPARED,
)
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_QUEUED,
    RECIPIENT_SCOPE_CONTROL_ONLY,
    SEND_MODE_LIVE,
    SEND_RUN_STATUS_QUEUED,
    WORKFLOW_TYPE_SIMPLE_MAILING,
)
from marketing.tests.test_marketing_campaign_send import make_test_send_template
from marketing.tests.test_marketing_campaigns import make_audience, make_campaign


class MarketingAdminRegistrationTests(TestCase):
    def test_template_admin_registered(self):
        self.assertTrue(admin.site.is_registered(MarketingWhatsAppTemplate))

    def test_send_run_admin_registered(self):
        self.assertTrue(admin.site.is_registered(MarketingCampaignSendRun))

    def test_message_admin_registered(self):
        self.assertTrue(admin.site.is_registered(MarketingCampaignMessage))

    def test_core_whatsapp_message_log_untouched(self):
        self.assertTrue(admin.site.is_registered(WhatsAppMessageLog))
        self.assertIsInstance(
            admin.site._registry[WhatsAppMessageLog],
            WhatsAppMessageLogAdmin,
        )


class MarketingAdminPermissionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.superuser = User.objects.create_superuser('admin', 'admin@test.com', 'pass')
        self.request = self.factory.get('/admin/')
        self.request.user = self.superuser

    def test_send_run_add_forbidden(self):
        admin_obj = MarketingCampaignSendRunAdmin(MarketingCampaignSendRun, self.site)
        self.assertFalse(admin_obj.has_add_permission(self.request))

    def test_send_run_delete_forbidden(self):
        admin_obj = MarketingCampaignSendRunAdmin(MarketingCampaignSendRun, self.site)
        self.assertFalse(admin_obj.has_delete_permission(self.request))

    def test_send_run_change_forbidden(self):
        admin_obj = MarketingCampaignSendRunAdmin(MarketingCampaignSendRun, self.site)
        self.assertFalse(admin_obj.has_change_permission(self.request))

    def test_message_add_forbidden(self):
        admin_obj = MarketingCampaignMessageAdmin(MarketingCampaignMessage, self.site)
        self.assertFalse(admin_obj.has_add_permission(self.request))

    def test_message_delete_forbidden(self):
        admin_obj = MarketingCampaignMessageAdmin(MarketingCampaignMessage, self.site)
        self.assertFalse(admin_obj.has_delete_permission(self.request))

    def test_message_change_forbidden(self):
        admin_obj = MarketingCampaignMessageAdmin(MarketingCampaignMessage, self.site)
        self.assertFalse(admin_obj.has_change_permission(self.request))

    def test_message_fields_readonly(self):
        admin_obj = MarketingCampaignMessageAdmin(MarketingCampaignMessage, self.site)
        readonly = set(admin_obj.readonly_fields)
        for field_name in (
            'status',
            'phone_normalized',
            'variables',
            'meta_message_id',
            'error_code',
            'safe_error_message_display',
            'wave_number',
            'position_number',
            'scheduled_at',
        ):
            self.assertIn(field_name, readonly)


class MarketingAdminDisplayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin', 'admin@test.com', 'pass')
        self.client = Client()
        self.client.force_login(self.user)
        self.template = make_test_send_template(self.user)
        self.campaign = make_campaign(
            make_audience(name='Admin test audience', created_by=self.user),
            self.user,
            name='Admin test campaign',
            channel=CHANNEL_WHATSAPP,
            status=STATUS_AUDIENCE_PREPARED,
            message_template=self.template,
        )
        self.recipient = MarketingCampaignRecipient.objects.create(
            campaign=self.campaign,
            phone_normalized='77001112233',
            display_name='Buyer',
            city='Алматы',
            roles=['Покупатель'],
            vehicle_summary='Toyota',
            is_test_contact=False,
            is_control_recipient=True,
            consent_status='granted',
            eligibility_status=ELIGIBILITY_ELIGIBLE,
        )
        self.send_run = MarketingCampaignSendRun.objects.create(
            campaign=self.campaign,
            template=self.template,
            mode=SEND_MODE_LIVE,
            status=SEND_RUN_STATUS_QUEUED,
            workflow_type=WORKFLOW_TYPE_SIMPLE_MAILING,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
            total_count=1,
            queued_count=1,
            created_by=self.user,
            started_at=timezone.now(),
        )
        self.message = MarketingCampaignMessage.objects.create(
            send_run=self.send_run,
            campaign_recipient=self.recipient,
            phone_normalized='77001112233',
            template_name=self.template.meta_template_name,
            language_code=self.template.language_code,
            variables={'request_history_url': 'https://zpt.kz/my-requests/token/'},
            status=MESSAGE_STATUS_QUEUED,
            meta_message_id='wamid.admin.test.message',
            error_code='test_error',
            error_message='Meta rejected template variable',
            wave_number=1,
            position_number=1,
            scheduled_at=timezone.now(),
        )

    def test_control_display_uses_recipient_snapshot_not_buyer_contact(self):
        from marketing.tests.test_marketing_audiences import make_buyer

        buyer = make_buyer(phone_normalized='77001112233', is_control_recipient=False)
        self.assertFalse(buyer.is_control_recipient)
        admin_obj = MarketingCampaignMessageAdmin(MarketingCampaignMessage, AdminSite())
        self.assertEqual(admin_obj.control_display(self.message), 'CONTROL')
        buyer.is_control_recipient = True
        buyer.save(update_fields=['is_control_recipient'])
        self.assertEqual(admin_obj.control_display(self.message), 'CONTROL')
        self.recipient.is_control_recipient = False
        self.recipient.save(update_fields=['is_control_recipient'])
        self.message.refresh_from_db()
        self.assertEqual(admin_obj.control_display(self.message), '—')

    def test_campaign_short_display_uses_started_at(self):
        started = timezone.make_aware(datetime(2026, 7, 31, 15, 30))
        MarketingCampaignSendRun.objects.filter(pk=self.send_run.pk).update(
            started_at=started,
        )
        self.send_run.refresh_from_db()
        admin_obj = MarketingCampaignMessageAdmin(MarketingCampaignMessage, AdminSite())
        html = str(admin_obj.campaign_short_display(self.message))
        expected_date = timezone.localtime(started).strftime('%d.%m.%Y')
        self.assertIn(f'Кампания №{self.campaign.pk} — {expected_date}', html)
        self.assertIn(
            reverse('marketing:campaign_detail', args=[self.campaign.pk]),
            html,
        )
        self.assertIn(f'title="{self.campaign.name}"', html)

    def test_campaign_short_display_falls_back_to_send_run_created_at(self):
        created = timezone.make_aware(datetime(2026, 6, 15, 9, 0))
        MarketingCampaignSendRun.objects.filter(pk=self.send_run.pk).update(
            started_at=None,
            created_at=created,
        )
        self.send_run.refresh_from_db()
        admin_obj = MarketingCampaignMessageAdmin(MarketingCampaignMessage, AdminSite())
        html = str(admin_obj.campaign_short_display(self.message))
        expected_date = timezone.localtime(created).strftime('%d.%m.%Y')
        self.assertIn(f'Кампания №{self.campaign.pk} — {expected_date}', html)

    def test_message_changelist_shows_short_campaign_display(self):
        url = reverse('admin:marketing_marketingcampaignmessage_changelist')
        response = self.client.get(url, {'send_run': str(self.send_run.pk)})
        self.assertEqual(response.status_code, 200)
        expected_date = timezone.localtime(self.send_run.started_at).strftime('%d.%m.%Y')
        self.assertContains(
            response,
            f'Кампания №{self.campaign.pk} — {expected_date}',
        )
        self.assertContains(
            response,
            reverse('marketing:campaign_detail', args=[self.campaign.pk]),
        )
        self.assertContains(response, f'title="{self.campaign.name}"')

    def test_message_change_shows_full_campaign_name(self):
        url = reverse(
            'admin:marketing_marketingcampaignmessage_change',
            args=[self.message.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.campaign.name)

    def test_search_by_phone_and_meta_message_id(self):
        url = reverse('admin:marketing_marketingcampaignmessage_changelist')
        by_phone = self.client.get(url, {'q': '77001112233'})
        self.assertEqual(by_phone.status_code, 200)
        self.assertContains(by_phone, str(self.message.pk))
        by_meta = self.client.get(url, {'q': 'wamid.admin.test.message'})
        self.assertEqual(by_meta.status_code, 200)
        self.assertContains(by_meta, str(self.message.pk))

    def test_message_filters_do_not_break_changelist(self):
        url = reverse('admin:marketing_marketingcampaignmessage_changelist')
        response = self.client.get(
            url,
            {
                'status': MESSAGE_STATUS_QUEUED,
                'workflow_type': WORKFLOW_TYPE_SIMPLE_MAILING,
                'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
                'control': 'yes',
                'wave_number': '1',
                'send_run': str(self.send_run.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '7700***2233')
        self.assertContains(response, 'CONTROL')

    def test_send_run_messages_link_points_to_filtered_changelist(self):
        factory = RequestFactory()
        request = factory.get('/admin/')
        request.user = self.user
        admin_obj = MarketingCampaignSendRunAdmin(MarketingCampaignSendRun, AdminSite())
        run = admin_obj.get_queryset(request).get(pk=self.send_run.pk)
        html = admin_obj.messages_link(run)
        self.assertIn(f'?send_run={self.send_run.pk}', html)
        changelist = self.client.get(
            reverse('admin:marketing_marketingcampaignmessage_changelist'),
            {'send_run': str(self.send_run.pk)},
        )
        self.assertEqual(changelist.status_code, 200)
        self.assertContains(changelist, str(self.message.pk))

    def test_send_run_changelist_shows_scope_and_waves(self):
        url = reverse('admin:marketing_marketingcampaignsendrun_changelist')
        response = self.client.get(url, {'q': 'Admin test campaign'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Контрольная')
        self.assertContains(response, str(self.send_run.pk))

    def test_template_admin_changelist(self):
        url = reverse('admin:marketing_marketingwhatsapptemplate_changelist')
        response = self.client.get(url, {'q': self.template.meta_template_name})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.template.name)
