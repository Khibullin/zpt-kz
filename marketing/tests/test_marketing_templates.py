from __future__ import annotations

from unittest.mock import patch

from django.db import IntegrityError
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import BuyerBroadcastRecipient
from marketing.models import (
    MarketingCampaign,
    MarketingCampaignRecipient,
    MarketingWhatsAppTemplate,
)
from marketing.services.campaigns.constants import (
    PURPOSE_MARKETPLACE_BUYERS,
    PURPOSE_PARTS_BUYERS,
    PURPOSE_TEST_CAMPAIGN,
    STATUS_AUDIENCE_PREPARED,
)
from marketing.services.campaigns.readiness import build_campaign_readiness
from marketing.services.templates.constants import (
    META_STATUS_APPROVED,
    META_STATUS_DISABLED,
    META_STATUS_DRAFT,
    META_STATUS_PAUSED,
    META_STATUS_REJECTED,
    get_reserved_service_template_names,
)
from marketing.services.templates.selectors import template_is_compatible_with_campaign
from marketing.services.templates.validation import (
    TemplateValidationError,
    validate_buttons,
    validate_meta_template_name,
    validate_variables,
)
from marketing.tests.test_marketing_campaigns import make_audience, make_campaign
from marketing.tests.test_marketing_audiences import grant_marketing_permission, next_phone


def make_template(user: User, **kwargs) -> MarketingWhatsAppTemplate:
    suffix = next_phone()
    defaults = {
        'name': f'Marketing template {suffix}',
        'meta_template_name': f'zpt_marketing_{suffix}',
        'language_code': 'ru',
        'meta_status': META_STATUS_APPROVED,
        'is_active': True,
        'allowed_purposes': [PURPOSE_PARTS_BUYERS],
        'body_text': 'Здравствуйте, {{recipient_name}}!',
        'variables': [{
            'key': 'recipient_name',
            'label': 'Имя получателя',
            'required': False,
            'example': 'Алексей',
        }],
        'created_by': user,
    }
    defaults.update(kwargs)
    return MarketingWhatsAppTemplate.objects.create(**defaults)


class MarketingTemplateAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)

    def test_permission_required(self):
        response = self.client.get(reverse('marketing:templates'))
        self.assertEqual(response.status_code, 302)
        self.client.login(username='marketer', password='secret')
        response = self.client.get(reverse('marketing:templates'))
        self.assertEqual(response.status_code, 200)


class MarketingTemplateCrudTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')

    def test_create_marketing_template(self):
        response = self.client.post(
            reverse('marketing:template_create'),
            {
                'name': 'Promo buyers',
                'meta_template_name': 'zpt_promo_buyers',
                'language_code': 'ru',
                'meta_status': META_STATUS_APPROVED,
                'is_active': 'on',
                'allowed_purposes': PURPOSE_PARTS_BUYERS,
                'body_text': 'Hello {{recipient_name}}',
                'variable_key_0': 'recipient_name',
                'variable_label_0': 'Имя получателя',
                'variable_example_0': 'Алексей',
            },
        )
        self.assertEqual(response.status_code, 302)
        template = MarketingWhatsAppTemplate.objects.get(name='Promo buyers')
        self.assertEqual(template.meta_template_name, 'zpt_promo_buyers')
        self.assertEqual(template.allowed_purposes, [PURPOSE_PARTS_BUYERS])

    def test_name_required(self):
        response = self.client.post(
            reverse('marketing:template_create'),
            {
                'name': '',
                'meta_template_name': 'zpt_promo_buyers',
                'language_code': 'ru',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingWhatsAppTemplate.objects.filter(meta_template_name='zpt_promo_buyers').exists())

    def test_meta_template_name_validated(self):
        response = self.client.post(
            reverse('marketing:template_create'),
            {
                'name': 'Bad meta name',
                'meta_template_name': 'Invalid-Name',
                'language_code': 'ru',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingWhatsAppTemplate.objects.filter(name='Bad meta name').exists())

    def test_allowed_purposes_allowlist(self):
        template = make_template(self.user, allowed_purposes=[PURPOSE_PARTS_BUYERS])
        self.assertEqual(template.allowed_purposes, [PURPOSE_PARTS_BUYERS])

    def test_unknown_purpose_rejected(self):
        with self.assertRaises(ValidationError):
            MarketingWhatsAppTemplate.objects.create(
                name='Bad purpose template',
                meta_template_name='zpt_bad_purpose',
                language_code='ru',
                allowed_purposes=['unknown_purpose'],
            )

    def test_variables_safe_structure(self):
        validated = validate_variables([{
            'key': 'recipient_name',
            'label': 'Имя',
            'required': True,
            'example': 'Алексей',
        }])
        self.assertEqual(validated[0]['key'], 'recipient_name')

    def test_duplicate_variable_key_rejected(self):
        with self.assertRaises(TemplateValidationError):
            validate_variables([
                {'key': 'recipient_name', 'label': 'One', 'required': False, 'example': 'A'},
                {'key': 'recipient_name', 'label': 'Two', 'required': False, 'example': 'B'},
            ])

    def test_forbidden_variable_fields_rejected(self):
        with self.assertRaises(TemplateValidationError):
            validate_variables([{
                'key': 'recipient_name',
                'label': 'Имя',
                'phone': '77001234567',
            }])

    def test_javascript_url_in_buttons_rejected(self):
        with self.assertRaises(TemplateValidationError):
            validate_buttons([{
                'type': 'url',
                'text': 'Click',
                'value': 'javascript:alert(1)',
            }])

    def test_copy_creates_separate_template(self):
        source = make_template(self.user, name='Promo original')
        response = self.client.post(reverse('marketing:template_copy', args=[source.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MarketingWhatsAppTemplate.objects.count(), 2)
        copy = MarketingWhatsAppTemplate.objects.exclude(pk=source.pk).get()
        self.assertEqual(copy.name, 'Копия — Promo original')
        self.assertFalse(copy.is_active)
        self.assertEqual(copy.body_text, source.body_text)
        self.assertNotEqual(copy.meta_template_name, source.meta_template_name)

    def test_two_sequential_copies_use_incremental_names(self):
        source = make_template(self.user, name='Repeat promo')
        self.client.post(reverse('marketing:template_copy', args=[source.pk]))
        self.client.post(reverse('marketing:template_copy', args=[source.pk]))
        names = set(
            MarketingWhatsAppTemplate.objects.exclude(pk=source.pk).values_list('name', flat=True),
        )
        self.assertEqual(
            names,
            {'Копия — Repeat promo', 'Копия 2 — Repeat promo'},
        )
        self.assertEqual(MarketingWhatsAppTemplate.objects.count(), 3)

    def test_delete_forbidden_when_used_by_campaign(self):
        template = make_template(self.user)
        audience = make_audience()
        campaign = make_campaign(audience, self.user, message_template=template)
        response = self.client.post(
            reverse('marketing:template_delete', args=[template.pk]),
            {'confirm': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(MarketingWhatsAppTemplate.objects.filter(pk=template.pk).exists())
        self.assertTrue(MarketingCampaign.objects.filter(pk=campaign.pk).exists())

    def test_get_does_not_change_state(self):
        template = make_template(self.user, is_active=True)
        updated_at = template.updated_at
        response = self.client.get(reverse('marketing:template_detail', args=[template.pk]))
        self.assertEqual(response.status_code, 200)
        template.refresh_from_db()
        self.assertEqual(template.updated_at, updated_at)

    def test_no_send_url_route(self):
        from marketing import urls as marketing_urls

        marketing_route_names = {pattern.name for pattern in marketing_urls.urlpatterns if pattern.name}
        self.assertNotIn('template_send', marketing_route_names)
        self.assertNotIn('campaign_send', marketing_route_names)

    def test_no_send_button_on_templates_list(self):
        make_template(self.user)
        response = self.client.get(reverse('marketing:templates'))
        content = response.content.decode('utf-8').lower()
        self.assertNotIn('отправить', content)
        self.assertNotIn('sync', content)
        self.assertNotIn('meta api', content)

    @patch('core.whatsapp_template_sender.send_whatsapp_template_message')
    def test_whatsapp_template_sender_not_called(self, mocked_send):
        template = make_template(self.user)
        audience = make_audience()
        self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': 'Campaign with template',
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': audience.pk,
                'message_template': template.pk,
                'is_active': 'on',
            },
        )
        mocked_send.assert_not_called()

    def test_buyer_broadcast_recipient_not_created(self):
        template = make_template(self.user)
        audience = make_audience()
        self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': 'Campaign no broadcast recipient',
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': audience.pk,
                'message_template': template.pk,
                'is_active': 'on',
            },
        )
        self.assertEqual(BuyerBroadcastRecipient.objects.count(), 0)

    def test_full_phone_not_in_template_preview(self):
        template = make_template(
            self.user,
            name='Preview phone safety template',
            meta_template_name='zpt_preview_phone_safe',
            body_text='Здравствуйте, {{recipient_name}}!',
            variables=[{
                'key': 'recipient_name',
                'label': 'Имя',
                'required': False,
                'example': 'Алексей',
            }],
        )
        response = self.client.get(reverse('marketing:template_detail', args=[template.pk]))
        preview_section = response.content.decode('utf-8').split('wa-template-preview', 1)[1]
        self.assertIn('Алексей', preview_section)
        self.assertNotRegex(preview_section, r'77\d{9}')

    def test_service_templates_not_auto_imported(self):
        self.assertEqual(MarketingWhatsAppTemplate.objects.count(), 0)
        for service_name in get_reserved_service_template_names():
            self.assertFalse(
                MarketingWhatsAppTemplate.objects.filter(meta_template_name=service_name).exists(),
            )


class MarketingTemplateCampaignSelectionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.audience = make_audience()

    def _post_campaign(self, **extra):
        data = {
            'name': f'Campaign {next_phone()}',
            'purpose': PURPOSE_PARTS_BUYERS,
            'audience': self.audience.pk,
            'is_active': 'on',
        }
        data.update(extra)
        return self.client.post(reverse('marketing:campaign_create'), data)

    def test_approved_active_template_available(self):
        template = make_template(self.user)
        response = self._post_campaign(message_template=str(template.pk))
        self.assertEqual(response.status_code, 302)
        campaign = MarketingCampaign.objects.latest('id')
        self.assertEqual(campaign.message_template_id, template.pk)

    def test_draft_template_unavailable(self):
        template = make_template(self.user, meta_status=META_STATUS_DRAFT)
        response = self._post_campaign(message_template=str(template.pk))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            MarketingCampaign.objects.filter(message_template=template).exists(),
        )

    def test_rejected_template_unavailable(self):
        template = make_template(self.user, meta_status=META_STATUS_REJECTED)
        response = self._post_campaign(message_template=str(template.pk))
        self.assertEqual(response.status_code, 200)

    def test_paused_template_unavailable(self):
        template = make_template(self.user, meta_status=META_STATUS_PAUSED)
        response = self._post_campaign(message_template=str(template.pk))
        self.assertEqual(response.status_code, 200)

    def test_disabled_template_unavailable(self):
        template = make_template(self.user, meta_status=META_STATUS_DISABLED)
        response = self._post_campaign(message_template=str(template.pk))
        self.assertEqual(response.status_code, 200)

    def test_inactive_approved_template_unavailable(self):
        template = make_template(self.user, is_active=False)
        response = self._post_campaign(message_template=str(template.pk))
        self.assertEqual(response.status_code, 200)

    def test_incompatible_purpose_unavailable(self):
        template = make_template(self.user, allowed_purposes=[PURPOSE_MARKETPLACE_BUYERS])
        response = self._post_campaign(
            purpose=PURPOSE_PARTS_BUYERS,
            message_template=str(template.pk),
        )
        self.assertEqual(response.status_code, 200)

    def test_post_template_id_tampering_rejected(self):
        template = make_template(
            self.user,
            allow_test_campaign=True,
            allowed_purposes=[],
        )
        response = self._post_campaign(
            purpose=PURPOSE_PARTS_BUYERS,
            message_template=str(template.pk),
        )
        self.assertEqual(response.status_code, 200)

    def test_test_campaign_requires_allow_test_campaign(self):
        allowed = make_template(
            self.user,
            allow_test_campaign=True,
            allowed_purposes=[],
        )
        blocked = make_template(self.user, allow_test_campaign=False, allowed_purposes=[])
        test_audience = make_audience(
            contact_group='test_contacts',
            contact_subtype='test_contacts',
        )
        ok_response = self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': 'Test campaign ok',
                'purpose': PURPOSE_TEST_CAMPAIGN,
                'audience': test_audience.pk,
                'message_template': allowed.pk,
                'is_active': 'on',
            },
        )
        self.assertEqual(ok_response.status_code, 302)
        bad_response = self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': 'Test campaign bad',
                'purpose': PURPOSE_TEST_CAMPAIGN,
                'audience': test_audience.pk,
                'message_template': blocked.pk,
                'is_active': 'on',
            },
        )
        self.assertEqual(bad_response.status_code, 200)


class MarketingTemplateCampaignChangeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.audience = make_audience()
        self.template_a = make_template(self.user, name='Template A', meta_template_name='zpt_template_a')
        self.template_b = make_template(self.user, name='Template B', meta_template_name='zpt_template_b')
        self.campaign = make_campaign(
            self.audience,
            self.user,
            message_template=self.template_a,
            template_selected_at=timezone.now(),
            status=STATUS_AUDIENCE_PREPARED,
            audience_prepared_at=timezone.now(),
            eligible_count=2,
        )
        MarketingCampaignRecipient.objects.create(
            campaign=self.campaign,
            phone_normalized='77001112233',
            eligibility_status='eligible',
        )

    def test_template_change_does_not_delete_recipients(self):
        self.client.post(
            reverse('marketing:campaign_edit', args=[self.campaign.pk]),
            {
                'name': self.campaign.name,
                'purpose': self.campaign.purpose,
                'audience': self.audience.pk,
                'message_template': self.template_b.pk,
                'is_active': 'on',
            },
        )
        self.assertEqual(self.campaign.recipients.count(), 1)

    def test_template_change_does_not_reset_audience_prepared_at(self):
        prepared_at = self.campaign.audience_prepared_at
        self.client.post(
            reverse('marketing:campaign_edit', args=[self.campaign.pk]),
            {
                'name': self.campaign.name,
                'purpose': self.campaign.purpose,
                'audience': self.audience.pk,
                'message_template': self.template_b.pk,
                'is_active': 'on',
            },
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.audience_prepared_at, prepared_at)

    def test_deactivation_makes_readiness_false(self):
        readiness = build_campaign_readiness(self.campaign)
        self.assertTrue(readiness['template_ready'])
        self.template_a.is_active = False
        self.template_a.save()
        self.campaign.refresh_from_db()
        readiness = build_campaign_readiness(self.campaign)
        self.assertFalse(readiness['template_ready'])

    def test_meta_status_rejected_makes_readiness_false(self):
        self.template_a.meta_status = META_STATUS_REJECTED
        self.template_a.save()
        self.campaign.refresh_from_db()
        readiness = build_campaign_readiness(self.campaign)
        self.assertFalse(readiness['template_ready'])

    def test_campaign_detail_shows_readiness_block(self):
        response = self.client.get(reverse('marketing:campaign_detail', args=[self.campaign.pk]))
        content = response.content.decode('utf-8')
        self.assertIn('Готовность кампании', content)
        self.assertIn('Получатели готовы', content)
        self.assertIn('Шаблон готов', content)
        self.assertNotIn('Отправить', content)


class MarketingTemplateSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')

    def test_duplicate_meta_template_name_and_language_rejected(self):
        make_template(self.user, meta_template_name='zpt_unique_meta', language_code='ru')
        with self.assertRaises((ValidationError, IntegrityError)):
            make_template(
                self.user,
                name='Second internal name',
                meta_template_name='zpt_unique_meta',
                language_code='ru',
            )

    def test_same_meta_name_different_language_allowed(self):
        make_template(self.user, meta_template_name='zpt_shared_lang', language_code='ru')
        second = make_template(
            self.user,
            name='Shared lang EN',
            meta_template_name='zpt_shared_lang',
            language_code='en',
        )
        self.assertEqual(second.language_code, 'en')

    def test_meta_template_name_case_normalized_and_blocks_duplicate(self):
        make_template(self.user, meta_template_name='zpt_case_norm', language_code='ru')
        with self.assertRaises((ValidationError, IntegrityError)):
            MarketingWhatsAppTemplate.objects.create(
                name='Case duplicate',
                meta_template_name='ZPT_CASE_NORM',
                language_code='ru',
                allowed_purposes=[PURPOSE_PARTS_BUYERS],
            )

    def test_invalid_meta_template_name_rejected(self):
        with self.assertRaises(TemplateValidationError):
            validate_meta_template_name('Bad-Name')

    def test_each_reserved_service_name_rejected_on_create(self):
        for index, reserved_name in enumerate(get_reserved_service_template_names()):
            response = self.client.post(
                reverse('marketing:template_create'),
                {
                    'name': f'Reserved attempt {index}',
                    'meta_template_name': reserved_name,
                    'language_code': 'ru',
                    'allowed_purposes': PURPOSE_PARTS_BUYERS,
                },
            )
            self.assertEqual(response.status_code, 200, msg=reserved_name)
            self.assertFalse(
                MarketingWhatsAppTemplate.objects.filter(name=f'Reserved attempt {index}').exists(),
            )

    def test_reserved_name_case_change_does_not_bypass(self):
        response = self.client.post(
            reverse('marketing:template_create'),
            {
                'name': 'Reserved uppercase',
                'meta_template_name': 'MP_REQUEST_V1',
                'language_code': 'ru',
                'allowed_purposes': PURPOSE_PARTS_BUYERS,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingWhatsAppTemplate.objects.filter(name='Reserved uppercase').exists())

    def test_non_reserved_marketing_template_created(self):
        template = make_template(self.user, meta_template_name='zpt_marketing_safe_name')
        self.assertEqual(template.meta_template_name, 'zpt_marketing_safe_name')

    def test_reserved_template_cannot_bind_to_campaign(self):
        MarketingWhatsAppTemplate.objects.bulk_create([
            MarketingWhatsAppTemplate(
                name='Reserved bulk',
                meta_template_name='zpt_buyer_request_receipt',
                language_code='ru',
                meta_status=META_STATUS_APPROVED,
                is_active=True,
                allowed_purposes=[PURPOSE_PARTS_BUYERS],
            ),
        ])
        reserved = MarketingWhatsAppTemplate.objects.get(name='Reserved bulk')
        self.assertFalse(
            template_is_compatible_with_campaign(reserved, purpose=PURPOSE_PARTS_BUYERS),
        )
        audience = make_audience()
        response = self.client.post(
            reverse('marketing:campaign_create'),
            {
                'name': 'Campaign reserved template',
                'purpose': PURPOSE_PARTS_BUYERS,
                'audience': audience.pk,
                'message_template': reserved.pk,
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            MarketingCampaign.objects.filter(name='Campaign reserved template').exists(),
        )

    def test_xss_payloads_are_escaped_in_template_detail(self):
        template = make_template(
            self.user,
            header_text='<script>alert(1)</script>',
            body_text='Hello {{recipient_name}} <img src=x onerror=alert(1)>',
            footer_text='<script>footer</script>',
            internal_notes='<script>notes</script>',
            variables=[{
                'key': 'recipient_name',
                'label': '<script>label</script>',
                'required': False,
                'example': '<script>example</script>',
            }],
            buttons=[{
                'type': 'quick_reply',
                'text': '<script>btn</script>',
                'value': 'safe_reply',
            }],
        )
        response = self.client.get(reverse('marketing:template_detail', args=[template.pk]))
        content = response.content.decode('utf-8')
        preview_start = content.index('class="wa-template-preview">') + len('class="wa-template-preview">')
        preview_end = content.index('</div>', content.index('wa-template-preview__buttons'))
        preview_only = content[preview_start:preview_end]
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', preview_only)
        self.assertNotIn('<script>alert', preview_only)
        self.assertNotIn('<img', preview_only)
        self.assertIn('&lt;script&gt;label&lt;/script&gt;', content)

    def test_javascript_button_url_rejected_on_create(self):
        response = self.client.post(
            reverse('marketing:template_create'),
            {
                'name': 'Bad button url',
                'meta_template_name': 'zpt_bad_button',
                'language_code': 'ru',
                'allowed_purposes': PURPOSE_PARTS_BUYERS,
                'button_type_0': 'url',
                'button_text_0': 'Click',
                'button_value_0': 'javascript:alert(1)',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingWhatsAppTemplate.objects.filter(name='Bad button url').exists())

    def test_quick_reply_rejects_url_like_value(self):
        with self.assertRaises(TemplateValidationError):
            validate_buttons([{
                'type': 'quick_reply',
                'text': 'Reply',
                'value': 'javascript:alert(1)',
            }])

    def test_category_post_tampering_ignored(self):
        response = self.client.post(
            reverse('marketing:template_create'),
            {
                'name': 'Category safe',
                'meta_template_name': 'zpt_category_safe',
                'language_code': 'ru',
                'meta_status': META_STATUS_APPROVED,
                'category': 'utility',
                'allowed_purposes': PURPOSE_PARTS_BUYERS,
            },
        )
        self.assertEqual(response.status_code, 302)
        template = MarketingWhatsAppTemplate.objects.get(name='Category safe')
        self.assertEqual(template.category, 'marketing')
