from __future__ import annotations

import uuid
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import (
    Brand,
    CONTACT_CONSENT_STATUS_GRANTED,
    Country,
    Seller,
)
from core.phone_utils import normalize_kz_phone
from core.services.seller_identity import find_sellers_by_phone
from marketing.services.simple_mailing.brands import SimpleMailingValidationError
from marketing.services.simple_mailing.constants import (
    RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
    RECIPIENT_TYPE_SELLERS,
    SELLER_SELECT_FIRST_N,
)
from marketing.services.simple_mailing.launch import (
    SimpleMailingCountChangedError,
    launch_simple_mailing,
)
from marketing.services.simple_mailing.launch_recipients import resolve_simple_mailing_launch_recipients
from marketing.services.simple_mailing.recipients import resolve_simple_mailing_recipients
from marketing.services.simple_mailing.seller_picker import (
    first_n_seller_ids,
    list_seller_picker_rows,
    seller_is_marketing_send_eligible,
    validate_selected_seller_ids,
)
from marketing.services.simple_mailing.seller_selectability import (
    WHATSAPP_STATE_AMBIGUOUS,
    WHATSAPP_STATE_INVALID,
    WHATSAPP_STATE_READY,
    build_seller_phone_identity_index,
    classify_seller_whatsapp,
)
from marketing.tests.test_marketing_audiences import grant_marketing_permission, next_phone
from marketing.tests.test_marketing_simple_mailing_live import LIVE_SIMPLE_SETTINGS
from marketing.tests.test_marketing_simple_mailing_seller_live import (
    _make_seller_template,
    _seller_draft,
)
from marketing.tests.test_seller_marketing_consent import grant_seller_consent

PICKER_JS = (
    Path(__file__).resolve().parents[1]
    / 'static'
    / 'marketing'
    / 'simple-mailing-seller-picker.js'
)


class SimpleMailingSellerSelectabilityTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='Japan')
        self.toyota = Brand.objects.create(country=self.country, name='Toyota')
        self.bmw = Brand.objects.create(country=self.country, name='BMW')

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

    def _rows(self, brands=None, **kwargs):
        return list_seller_picker_rows(
            all_brands=False,
            brands=brands or ['Toyota'],
            **kwargs,
        )

    def _row_by_id(self, seller_id: int, **kwargs):
        for row in self._rows(**kwargs):
            if row.seller_id == seller_id:
                return row
        self.fail(f'Seller {seller_id} is not in picker rows')

    def test_identity_index_matches_find_sellers_by_phone(self):
        unique = self._seller()
        shared = next_phone()
        first = self._seller(whatsapp=shared)
        second = self._seller(brand='BMW', brand_fk=self.bmw, whatsapp=shared)
        invalid = self._seller(whatsapp='not-a-phone')
        index = build_seller_phone_identity_index()
        for seller in (unique, first, second, invalid):
            classification = classify_seller_whatsapp(seller, index)
            canonical = normalize_kz_phone(seller.whatsapp)
            if not canonical:
                self.assertEqual(classification.state, WHATSAPP_STATE_INVALID)
                continue
            found_ids = {item.pk for item in find_sellers_by_phone(canonical)}
            indexed_ids = set(index.seller_ids_for_canonical(canonical))
            self.assertEqual(found_ids, indexed_ids)

    def test_valid_unique_phone_is_selectable(self):
        seller = self._seller(name='Ready Shop')
        row = self._row_by_id(seller.pk)
        self.assertTrue(row.selectable)
        self.assertEqual(row.whatsapp_state, WHATSAPP_STATE_READY)
        self.assertEqual(row.whatsapp_status_label, 'Готов')
        self.assertEqual(
            validate_selected_seller_ids([seller.pk], all_brands=False, brands=['Toyota']),
            [seller.pk],
        )

    def test_invalid_phone_is_visible_disabled_and_rejected(self):
        seller = self._seller(name='Bad Phone', whatsapp='12345')
        row = self._row_by_id(seller.pk)
        self.assertEqual(row.name, 'Bad Phone')
        self.assertFalse(row.selectable)
        self.assertFalse(row.marketing_send_eligible)
        self.assertEqual(row.whatsapp_state, WHATSAPP_STATE_INVALID)
        self.assertEqual(row.whatsapp_status_label, 'Неверный номер')
        self.assertEqual(row.consent_url, '')
        with self.assertRaises(SimpleMailingValidationError):
            validate_selected_seller_ids([seller.pk], all_brands=False, brands=['Toyota'])

    def test_duplicate_canonical_phone_disables_both_rows(self):
        phone = next_phone()
        first = self._seller(name='Dup A', whatsapp=phone)
        second = self._seller(name='Dup B', whatsapp=phone)
        rows = {row.seller_id: row for row in self._rows()}
        self.assertIn(first.pk, rows)
        self.assertIn(second.pk, rows)
        for seller in (first, second):
            row = rows[seller.pk]
            self.assertFalse(row.selectable)
            self.assertEqual(row.whatsapp_state, WHATSAPP_STATE_AMBIGUOUS)
            self.assertEqual(
                row.whatsapp_status_label,
                'Номер используется несколькими продавцами',
            )
            self.assertEqual(row.consent_url, '')
            with self.assertRaises(SimpleMailingValidationError):
                validate_selected_seller_ids(
                    [seller.pk],
                    all_brands=False,
                    brands=['Toyota'],
                )

    def test_seven_and_eight_prefix_same_canonical_are_ambiguous(self):
        first = self._seller(name='Seven prefix', whatsapp='77474276210')
        second = self._seller(name='Eight prefix', whatsapp='87474276210')
        self.assertEqual(normalize_kz_phone(first.whatsapp), '77474276210')
        self.assertEqual(normalize_kz_phone(second.whatsapp), '77474276210')
        found = find_sellers_by_phone('77474276210')
        self.assertEqual({item.pk for item in found}, {first.pk, second.pk})
        index = build_seller_phone_identity_index()
        rows = {row.seller_id: row for row in self._rows()}
        for seller in (first, second):
            classification = classify_seller_whatsapp(seller, index)
            self.assertEqual(classification.state, WHATSAPP_STATE_AMBIGUOUS)
            self.assertFalse(rows[seller.pk].selectable)
            with self.assertRaises(SimpleMailingValidationError):
                validate_selected_seller_ids(
                    [seller.pk],
                    all_brands=False,
                    brands=['Toyota'],
                )

    def test_plus_seven_and_plain_seven_same_canonical_are_ambiguous(self):
        first = self._seller(name='Plus seven', whatsapp='+77474276210')
        second = self._seller(name='Plain seven', whatsapp='77474276210')
        self.assertEqual(normalize_kz_phone(first.whatsapp), '77474276210')
        self.assertEqual(normalize_kz_phone(second.whatsapp), '77474276210')
        found = find_sellers_by_phone('77474276210')
        self.assertEqual({item.pk for item in found}, {first.pk, second.pk})
        index = build_seller_phone_identity_index()
        rows = {row.seller_id: row for row in self._rows()}
        for seller in (first, second):
            classification = classify_seller_whatsapp(seller, index)
            self.assertEqual(classification.state, WHATSAPP_STATE_AMBIGUOUS)
            self.assertFalse(rows[seller.pk].selectable)
            with self.assertRaises(SimpleMailingValidationError):
                validate_selected_seller_ids(
                    [seller.pk],
                    all_brands=False,
                    brands=['Toyota'],
                )

    def test_duplicate_outside_brand_filter_still_ambiguous(self):
        phone = next_phone()
        toyota = self._seller(name='Toyota Dup', whatsapp=phone)
        self._seller(name='BMW Dup', brand='BMW', brand_fk=self.bmw, whatsapp=phone)
        row = self._row_by_id(toyota.pk)
        self.assertFalse(row.selectable)
        self.assertEqual(row.whatsapp_state, WHATSAPP_STATE_AMBIGUOUS)
        with self.assertRaises(SimpleMailingValidationError):
            validate_selected_seller_ids(
                [toyota.pk],
                all_brands=False,
                brands=['Toyota'],
            )

    def test_first_five_skips_invalid_and_ambiguous(self):
        self._seller(name='Invalid 1', whatsapp='bad-1')
        self._seller(name='Invalid 2', whatsapp='bad-2')
        shared = next_phone()
        self._seller(name='Ambiguous A', whatsapp=shared)
        self._seller(name='Ambiguous B', whatsapp=shared)
        valid = [self._seller(name=f'Ready {index}') for index in range(6)]
        rows = self._rows()
        selected = first_n_seller_ids(rows, n=SELLER_SELECT_FIRST_N)
        self.assertEqual(selected, [seller.pk for seller in valid[:5]])

    def test_first_five_js_skips_disabled_checkboxes(self):
        source = PICKER_JS.read_text(encoding='utf-8')
        self.assertIn('visibleSelectableRows', source)
        self.assertIn('checkbox.disabled', source)
        self.assertIn("getAttribute('data-selectable') === '1'", source)

    def test_preview_excludes_invalid_and_ambiguous_from_ordinary_count(self):
        self._seller(name='Ready')
        self._seller(name='Invalid', whatsapp='not-phone')
        shared = next_phone()
        self._seller(name='Amb A', whatsapp=shared)
        self._seller(name='Amb B', whatsapp=shared)
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(result.seller_found_count, 4)
        self.assertEqual(result.seller_invalid_whatsapp_count, 1)
        self.assertEqual(result.seller_ambiguous_whatsapp_count, 2)
        self.assertEqual(result.seller_selectable_count, 1)
        self.assertEqual(result.ordinary_count, 1)

    def test_launch_does_not_collapse_ambiguous_phone_into_one_recipient(self):
        phone = next_phone()
        first = self._seller(name='Amb A', whatsapp=phone)
        second = self._seller(name='Amb B', whatsapp=phone)
        rows = resolve_simple_mailing_launch_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            all_brands=False,
            brands=['Toyota'],
            selected_seller_ids=[first.pk, second.pk],
        )
        ordinary = [row for row in rows if not row.is_control_recipient]
        self.assertEqual(ordinary, [])

    def test_paused_inactive_and_not_receiving_are_rejected(self):
        paused = self._seller(name='Paused', is_paused=True)
        inactive = self._seller(name='Inactive', is_active=False)
        silent = self._seller(name='Silent', receive_requests=False)
        for seller in (paused, inactive, silent):
            with self.assertRaises(SimpleMailingValidationError):
                validate_selected_seller_ids(
                    [seller.pk],
                    all_brands=False,
                    brands=['Toyota'],
                )

    def test_consent_not_recorded_stays_selectable_but_not_send_eligible(self):
        seller = self._seller(name='No consent')
        row = self._row_by_id(seller.pk)
        self.assertTrue(row.selectable)
        self.assertFalse(row.marketing_send_eligible)
        self.assertEqual(row.consent_label, 'Не подтверждено')
        self.assertTrue(row.consent_url)
        self.assertFalse(seller_is_marketing_send_eligible(seller, row.consent_status, selectable=True))

    def test_granted_unique_is_selectable_and_send_eligible(self):
        seller = self._seller(name='Granted')
        grant_seller_consent(seller, CONTACT_CONSENT_STATUS_GRANTED)
        row = self._row_by_id(seller.pk)
        self.assertTrue(row.selectable)
        self.assertTrue(row.marketing_send_eligible)
        self.assertTrue(
            seller_is_marketing_send_eligible(
                seller,
                row.consent_status,
                selectable=True,
            )
        )


class SimpleMailingSellerSelectabilityViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer-select', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer-select', password='secret')
        self.url = reverse('marketing:new_mailing')
        self.country = Country.objects.create(name='Japan')
        Brand.objects.create(country=self.country, name='Toyota')

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

    def _preview(self):
        return self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
                'brands': ['Toyota'],
            },
        )

    def test_preview_shows_new_counters_and_whatsapp_status(self):
        ready = self._seller(name='Ready Shop')
        invalid = self._seller(name='Broken Shop', whatsapp='oops')
        shared = next_phone()
        self._seller(name='Twin A', whatsapp=shared)
        self._seller(name='Twin B', whatsapp=shared)
        response = self._preview()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Найдено продавцов')
        self.assertContains(response, 'Доступно для выбора')
        self.assertContains(response, 'Исключено по WhatsApp')
        self.assertContains(response, 'Итого доступных получателей')
        self.assertNotContains(response, 'Обычных получателей')
        self.assertEqual(response.context['seller_found_count'], 4)
        self.assertEqual(response.context['seller_selectable_count'], 1)
        self.assertEqual(response.context['seller_invalid_whatsapp_count'], 1)
        self.assertEqual(response.context['seller_ambiguous_whatsapp_count'], 2)
        html = response.content.decode()
        self.assertIn('Неверный номер', html)
        self.assertIn('Номер используется несколькими продавцами', html)
        self.assertIn('disabled', html)
        self.assertContains(response, 'Скопировать ссылку подтверждения')
        invalid_row = next(
            row for row in response.context['seller_rows'] if row.seller_id == invalid.pk
        )
        ready_row = next(
            row for row in response.context['seller_rows'] if row.seller_id == ready.pk
        )
        self.assertEqual(invalid_row.consent_url, '')
        self.assertTrue(ready_row.consent_url)

    def test_prepare_rejects_invalid_seller_posted_manually(self):
        invalid = self._seller(name='Broken Shop', whatsapp='oops')
        self._preview()
        response = self.client.post(
            self.url,
            {
                'action': 'prepare_selected',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
                'brands': ['Toyota'],
                'seller_ids': [str(invalid.pk)],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не входят в текущую аудиторию')


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingSellerSelectabilityLaunchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('select-launch', password='secret', is_staff=True)
        self.template = _make_seller_template(self.user)

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

    def test_seller_becoming_duplicate_after_prepare_stops_launch(self):
        seller = self._seller(name='Prepared')
        draft = _seller_draft(seller_ids=[seller.pk], template_id=self.template.pk)
        self.assertGreater(draft['count'], 0)
        self._seller(name='Later duplicate', whatsapp=seller.whatsapp)
        later_rows = resolve_simple_mailing_launch_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            all_brands=False,
            brands=['Toyota'],
            selected_seller_ids=[seller.pk],
        )
        ordinary = [row for row in later_rows if not row.is_control_recipient]
        self.assertEqual(ordinary, [])
        self.assertLess(len(later_rows), draft['count'])
        with self.assertRaises(SimpleMailingCountChangedError):
            launch_simple_mailing(
                draft=draft,
                template=self.template,
                created_by=self.user,
                launch_key=str(uuid.uuid4()),
            )

    def test_seller_phone_becoming_invalid_after_prepare_stops_launch(self):
        seller = self._seller(name='Prepared valid')
        draft = _seller_draft(seller_ids=[seller.pk], template_id=self.template.pk)
        seller.whatsapp = 'not-valid-anymore'
        seller.save(update_fields=['whatsapp'])
        later_rows = resolve_simple_mailing_launch_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            all_brands=False,
            brands=['Toyota'],
            selected_seller_ids=[seller.pk],
        )
        ordinary = [row for row in later_rows if not row.is_control_recipient]
        self.assertEqual(ordinary, [])
        self.assertLess(len(later_rows), draft['count'])
        with self.assertRaises(SimpleMailingCountChangedError):
            launch_simple_mailing(
                draft=draft,
                template=self.template,
                created_by=self.user,
                launch_key=str(uuid.uuid4()),
            )
