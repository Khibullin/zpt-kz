from __future__ import annotations

import re
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Seller
from marketing.new_mailing_views import (
    PREVIEW_LABEL_CONTROL_ONLY,
    PREVIEW_LABEL_DEFAULT,
    PREVIEW_LABEL_SELLERS,
    preview_action_label,
)
from marketing.services.simple_mailing.constants import (
    RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
    RECIPIENT_SCOPE_CONTROL_ONLY,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
    RECIPIENT_TYPE_SELLERS,
)
from marketing.tests.test_marketing_audiences import grant_marketing_permission, next_phone
from marketing.tests.test_marketing_simple_mailing_control import _make_control_buyer


def _input_tag(html: str, input_id: str) -> str:
    match = re.search(rf'<input[^>]*id="{re.escape(input_id)}"[^>]*>', html)
    return match.group(0) if match else ''


def _tag_has_attr(tag: str, attr: str) -> bool:
    return bool(re.search(rf'\s{re.escape(attr)}(?:[\s>]|=)', tag))


class ControlOnlyBrandUxTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('control-ux', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='control-ux', password='secret')
        self.url = reverse('marketing:new_mailing')
        self._seller(name='Audi Shop', brand='Audi')
        self._seller(name='BMW Shop', brand='BMW')

    def _seller(self, **kwargs) -> Seller:
        defaults = {
            'name': 'Seller',
            'whatsapp': next_phone(),
            'transport_type': 'car',
            'city': 'Алматы',
            'is_active': True,
            'is_test_seller': False,
            'is_paused': False,
            'receive_requests': True,
            'brand': 'Toyota',
        }
        defaults.update(kwargs)
        return Seller.objects.create(**defaults)

    def _sellers_control_get(self):
        return self.client.get(
            self.url,
            {
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
            },
        )

    def test_control_only_sellers_brand_controls_are_disabled(self):
        response = self._sellers_control_get()
        html = response.content.decode()
        self.assertTrue(response.context['control_only'])
        self.assertIn('id="brand-selection-fieldset"', html)
        self.assertRegex(html, r'id="brand-selection-fieldset"[^>]*\sdisabled')
        all_brands = _input_tag(html, 'all-brands-checkbox')
        self.assertTrue(all_brands)
        self.assertTrue(_tag_has_attr(all_brands, 'disabled'))
        self.assertFalse(_tag_has_attr(all_brands, 'checked'))
        self.assertContains(response, 'class="brand-checkbox"')
        self.assertContains(response, 'simple-mailing__card--muted')
        self.assertContains(
            response,
            'Марки автомобилей не применяются к контрольной рассылке.',
        )
        disabled_brands = re.findall(
            r'<input([^>]*class="brand-checkbox"[^>]*)>',
            html,
        )
        self.assertTrue(disabled_brands)
        for attrs in disabled_brands:
            self.assertRegex(attrs, r'\sdisabled(?:\s|>|=|$)')
            self.assertNotRegex(attrs, r'\schecked(?:\s|>|=|$)')

    def test_control_only_brand_search_is_disabled(self):
        response = self._sellers_control_get()
        search = _input_tag(response.content.decode(), 'brand-search')
        self.assertTrue(search)
        self.assertTrue(_tag_has_attr(search, 'disabled'))

    def test_control_only_preview_button_label(self):
        response = self._sellers_control_get()
        self.assertEqual(response.context['preview_action_label'], PREVIEW_LABEL_CONTROL_ONLY)
        self.assertContains(response, PREVIEW_LABEL_CONTROL_ONLY)
        self.assertNotContains(response, PREVIEW_LABEL_SELLERS)

    def test_control_only_posted_brands_still_use_only_controls(self):
        first = _make_control_buyer()
        second = _make_control_buyer()
        response = self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
                'all_brands': '1',
                'brands': ['Audi', 'BMW', 'BYD'],
            },
        )
        self.assertEqual(response.status_code, 200)
        result = response.context['result']
        self.assertEqual(result.ordinary_count, 0)
        self.assertEqual(result.control_count, 2)
        self.assertEqual(result.count, 2)
        self.assertFalse(response.context['show_seller_picker'])
        preview_phones = {row.masked_phone[-4:] for row in result.preview_rows}
        self.assertEqual(len(preview_phones), 2)
        self.assertTrue(all(row.recipient_type_label == 'Контрольный получатель' for row in result.preview_rows))
        self.assertNotEqual(first.phone_normalized, second.phone_normalized)

    def test_control_only_sellers_continue_button_after_preview(self):
        _make_control_buyer()
        _make_control_buyer()
        response = self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
            },
        )
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Продолжить к сообщению')
        self.assertNotContains(response, 'Подготовить выбранных')
        self.assertRegex(
            html,
            r'<button[^>]*name="action"[^>]*value="continue"|'
            r'<button[^>]*value="continue"[^>]*name="action"',
        )
        self.assertNotRegex(html, r'value="prepare_selected"')

    def test_control_only_post_does_not_check_all_brands(self):
        _make_control_buyer()
        response = self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
                'all_brands': '1',
                'brands': ['Audi', 'BMW'],
            },
        )
        html = response.content.decode()
        self.assertFalse(response.context['all_brands'])
        self.assertEqual(list(response.context['selected_brands']), [])
        all_brands = _input_tag(html, 'all-brands-checkbox')
        self.assertTrue(_tag_has_attr(all_brands, 'disabled'))
        self.assertFalse(_tag_has_attr(all_brands, 'checked'))
        for attrs in re.findall(r'<input([^>]*class="brand-checkbox"[^>]*)>', html):
            self.assertNotRegex(attrs, r'\schecked(?:\s|>|=|$)')

    def test_control_only_summary_shows_dash_for_brands(self):
        _make_control_buyer()
        _make_control_buyer()
        response = self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
                'brands': ['Audi', 'BMW'],
            },
        )
        self.assertEqual(response.context['selected_brands_label'], '—')
        self.assertEqual(response.context['ordinary_count_display'], '0')
        self.assertEqual(response.context['control_count_display'], '2')
        self.assertEqual(response.context['count_display'], '2')
        self.assertEqual(
            response.context['recipient_scope_label'],
            'Контрольная рассылка — только контрольные номера',
        )
        selected = re.search(
            r'id="selected-brands-count"[^>]*>([^<]+)<',
            response.content.decode(),
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.group(1).strip(), '—')

    def test_working_sellers_preview_button_label(self):
        response = self.client.get(
            self.url,
            {
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            },
        )
        self.assertEqual(response.context['preview_action_label'], PREVIEW_LABEL_SELLERS)
        self.assertContains(response, PREVIEW_LABEL_SELLERS)
        self.assertFalse(response.context['can_calculate'])
        self.assertRegex(
            response.content.decode(),
            r'<button[^>]*id="preview-button"[^>]*disabled',
        )

    def test_working_buyers_keep_default_preview_label(self):
        self.assertEqual(
            preview_action_label(
                recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
                recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            ),
            PREVIEW_LABEL_DEFAULT,
        )
        response = self.client.get(
            self.url,
            {
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            },
        )
        self.assertEqual(response.context['preview_action_label'], PREVIEW_LABEL_DEFAULT)
        self.assertContains(response, PREVIEW_LABEL_DEFAULT)


class SimpleMailingFormJsContractTests(TestCase):
    def setUp(self):
        self.js = Path('marketing/static/marketing/simple-mailing-form.js').read_text(
            encoding='utf-8',
        )

    def test_js_clears_and_disables_brands_when_switching_to_control_only(self):
        self.assertIn('function clearBrandSelection', self.js)
        self.assertIn('allBrands.checked = false', self.js)
        self.assertIn('checkbox.checked = false', self.js)
        self.assertIn('brandFieldset.disabled = controlOnly', self.js)
        self.assertIn('allBrands.disabled = controlOnly || marketplaceLocked', self.js)
        self.assertIn('brandSearch.disabled = controlOnly', self.js)
        self.assertIn('onScopeChanged', self.js)
        self.assertIn('clearBrandSelection()', self.js)

    def test_js_enables_working_mode_and_requires_brand_before_preview(self):
        self.assertIn('previewButton.disabled = !hasValidSelection()', self.js)
        self.assertIn('function filterBrandCards', self.js)
        self.assertIn('is-search-hidden', self.js)
        self.assertIn('function hasValidSelection', self.js)
        self.assertIn('isControlOnlyScope()', self.js)
        self.assertIn("selectedCountDisplay.textContent = controlOnly ? '—'", self.js)
        self.assertIn('PREVIEW_LABEL_CONTROL', self.js)
        self.assertIn('PREVIEW_LABEL_SELLERS', self.js)
        self.assertIn('marketplaceLocked && allBrands', self.js)
        self.assertIn('allBrands.checked = true', self.js)
