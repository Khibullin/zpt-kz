from importlib import import_module

from django.apps import apps
from django.test import TestCase

from marketing.models import (
    MarketingAudience,
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignRecipient,
    MarketingCampaignSendRun,
    MarketingWhatsAppTemplate,
)


LAUNCH = import_module('marketing.migrations.0012_prepare_ag_parts_wholesale_launch')


class AgPartsWholesaleLaunchMigrationTests(TestCase):
    def test_launch_objects_are_draft_without_recipients_or_sends(self):
        LAUNCH.prepare_ag_parts_wholesale_launch(apps, None)
        LAUNCH.prepare_ag_parts_wholesale_launch(apps, None)

        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name=LAUNCH.TEMPLATE_META_NAME,
            language_code=LAUNCH.TEMPLATE_LANGUAGE,
        )
        self.assertEqual(
            MarketingWhatsAppTemplate.objects.filter(
                meta_template_name=LAUNCH.TEMPLATE_META_NAME,
            ).count(),
            1,
        )
        self.assertEqual(template.meta_status, 'unknown')
        self.assertNotEqual(template.meta_status, 'approved')
        self.assertEqual(template.meta_template_name, 'zpt_ag_parts_wholesale_v1')
        self.assertTrue(template.allow_test_campaign)
        self.assertEqual(
            template.allowed_purposes,
            ['request_sellers', 'marketplace_sellers', 'combined_sellers'],
        )
        self.assertEqual(template.meta_template_id, '')
        self.assertEqual(template.variables, [])
        self.assertEqual(len(template.buttons), 1)
        button = template.buttons[0]
        self.assertEqual(button['type'], 'url')
        self.assertIn('utm_source=whatsapp', button['value'])
        self.assertIn('utm_medium=marketing', button['value'])
        self.assertIn('utm_campaign=ag_parts_wholesale_launch_202608', button['value'])
        self.assertEqual(template.body_text, LAUNCH.TEMPLATE_BODY)

        audience = MarketingAudience.objects.get(name=LAUNCH.AUDIENCE_NAME)
        self.assertEqual(
            MarketingAudience.objects.filter(name=LAUNCH.AUDIENCE_NAME).count(),
            1,
        )
        self.assertEqual(audience.contact_group, 'sellers')
        self.assertEqual(audience.contact_subtype, 'all_sellers')
        self.assertEqual(audience.criteria.get('brands'), LAUNCH.AUDIENCE_BRANDS)
        self.assertTrue(audience.criteria.get('is_active'))
        self.assertFalse(audience.criteria.get('is_test'))
        self.assertEqual(audience.last_matched_count, 0)
        self.assertIsNone(audience.last_calculated_at)

        campaign = MarketingCampaign.objects.get(name=LAUNCH.CAMPAIGN_NAME)
        self.assertEqual(
            MarketingCampaign.objects.filter(name=LAUNCH.CAMPAIGN_NAME).count(),
            1,
        )
        self.assertEqual(campaign.status, 'draft')
        self.assertEqual(campaign.channel, 'whatsapp')
        self.assertEqual(campaign.purpose, 'combined_sellers')
        self.assertEqual(campaign.message_template_id, template.pk)
        self.assertEqual(campaign.audience_id, audience.pk)
        self.assertIsNone(campaign.audience_prepared_at)

        self.assertEqual(
            MarketingCampaignRecipient.objects.filter(campaign=campaign).count(),
            0,
        )
        self.assertEqual(
            MarketingCampaignSendRun.objects.filter(campaign=campaign).count(),
            0,
        )
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(
                send_run__campaign=campaign,
            ).count(),
            0,
        )


class AgPartsWholesaleTemplateCopyMigrationTests(TestCase):
    def test_local_draft_header_and_first_line_updated(self):
        from django.apps import apps as django_apps

        COPY = import_module(
            'marketing.migrations.0013_ag_parts_wholesale_template_avtozapchasti'
        )

        LAUNCH.prepare_ag_parts_wholesale_launch(apps, None)
        COPY.update_ag_parts_wholesale_template_copy(django_apps, None)
        COPY.update_ag_parts_wholesale_template_copy(django_apps, None)

        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name=LAUNCH.TEMPLATE_META_NAME,
            language_code=LAUNCH.TEMPLATE_LANGUAGE,
        )
        self.assertEqual(template.meta_status, 'unknown')
        self.assertEqual(template.header_text, COPY.NEW_HEADER)
        self.assertTrue(template.body_text.startswith(COPY.NEW_FIRST_LINE))
        self.assertIn('Салонные фильтры — от 310 ₸/шт.', template.body_text)
        self.assertNotIn('автокомпонентов', template.body_text)
        self.assertNotIn('автокомпоненты', template.header_text.lower())

        campaign = MarketingCampaign.objects.get(name=LAUNCH.CAMPAIGN_NAME)
        self.assertEqual(campaign.status, 'draft')
        self.assertEqual(
            MarketingCampaignRecipient.objects.filter(campaign=campaign).count(),
            0,
        )
        self.assertEqual(
            MarketingCampaignSendRun.objects.filter(campaign=campaign).count(),
            0,
        )
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(
                send_run__campaign=campaign,
            ).count(),
            0,
        )

    def test_approved_template_is_not_rewritten(self):
        from django.apps import apps as django_apps

        COPY = import_module(
            'marketing.migrations.0013_ag_parts_wholesale_template_avtozapchasti'
        )
        LAUNCH.prepare_ag_parts_wholesale_launch(apps, None)
        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name=LAUNCH.TEMPLATE_META_NAME,
            language_code=LAUNCH.TEMPLATE_LANGUAGE,
        )
        template.meta_status = 'approved'
        template.header_text = 'Оптовые автокомпоненты AG Parts'
        template.save(update_fields=['meta_status', 'header_text'])

        COPY.update_ag_parts_wholesale_template_copy(django_apps, None)
        template.refresh_from_db()
        self.assertEqual(template.meta_status, 'approved')
        self.assertEqual(template.header_text, 'Оптовые автокомпоненты AG Parts')

    def test_missing_template_is_noop(self):
        from django.apps import apps as django_apps

        COPY = import_module(
            'marketing.migrations.0013_ag_parts_wholesale_template_avtozapchasti'
        )
        LAUNCH.prepare_ag_parts_wholesale_launch(apps, None)
        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name=LAUNCH.TEMPLATE_META_NAME,
            language_code=LAUNCH.TEMPLATE_LANGUAGE,
        )
        original_header = template.header_text
        original_body = template.body_text
        template.meta_template_name = 'zpt_ag_parts_wholesale_other'
        template.save(update_fields=['meta_template_name'])

        COPY.update_ag_parts_wholesale_template_copy(django_apps, None)
        template.refresh_from_db()
        self.assertEqual(template.header_text, original_header)
        self.assertEqual(template.body_text, original_body)


class AgPartsWholesaleTemplateBodyLineMigrationTests(TestCase):
    def test_first_body_line_updated_without_meta_or_campaign_side_effects(self):
        from django.apps import apps as django_apps

        COPY13 = import_module(
            'marketing.migrations.0013_ag_parts_wholesale_template_avtozapchasti'
        )
        COPY14 = import_module(
            'marketing.migrations.0014_ag_parts_wholesale_template_body_line'
        )

        LAUNCH.prepare_ag_parts_wholesale_launch(apps, None)
        COPY13.update_ag_parts_wholesale_template_copy(django_apps, None)
        COPY14.update_ag_parts_wholesale_template_first_line(django_apps, None)
        COPY14.update_ag_parts_wholesale_template_first_line(django_apps, None)

        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name=LAUNCH.TEMPLATE_META_NAME,
            language_code=LAUNCH.TEMPLATE_LANGUAGE,
        )
        self.assertEqual(template.meta_status, 'unknown')
        self.assertEqual(template.header_text, COPY14.NEW_HEADER)
        self.assertEqual(
            template.body_text.split('\n', 1)[0],
            COPY14.NEW_FIRST_LINE,
        )
        self.assertIn('Салонные фильтры — от 310 ₸/шт.', template.body_text)
        self.assertNotIn('автокомпонентов', template.body_text)

        campaign = MarketingCampaign.objects.get(name=LAUNCH.CAMPAIGN_NAME)
        self.assertEqual(campaign.status, 'draft')
        self.assertEqual(
            MarketingCampaignRecipient.objects.filter(campaign=campaign).count(),
            0,
        )
        self.assertEqual(
            MarketingCampaignSendRun.objects.filter(campaign=campaign).count(),
            0,
        )
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(
                send_run__campaign=campaign,
            ).count(),
            0,
        )

    def test_approved_template_is_not_rewritten(self):
        from django.apps import apps as django_apps

        COPY14 = import_module(
            'marketing.migrations.0014_ag_parts_wholesale_template_body_line'
        )
        LAUNCH.prepare_ag_parts_wholesale_launch(apps, None)
        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name=LAUNCH.TEMPLATE_META_NAME,
            language_code=LAUNCH.TEMPLATE_LANGUAGE,
        )
        template.meta_status = 'approved'
        template.header_text = 'Оптовые автозапчасти AG Parts'
        old_body = template.body_text
        template.save(update_fields=['meta_status', 'header_text'])

        COPY14.update_ag_parts_wholesale_template_first_line(django_apps, None)
        template.refresh_from_db()
        self.assertEqual(template.meta_status, 'approved')
        self.assertEqual(template.body_text, old_body)
