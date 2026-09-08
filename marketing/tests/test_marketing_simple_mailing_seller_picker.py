from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.models import (
    Brand,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    Country,
    Seller,
)
from marketing.services.simple_mailing.brands import SimpleMailingValidationError
from marketing.services.simple_mailing.constants import (
    RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
    RECIPIENT_TYPE_SELLERS,
    SELLER_SELECT_FIRST_N,
)
from marketing.services.simple_mailing.draft import load_simple_mailing_draft
from marketing.services.simple_mailing.launch_recipients import resolve_simple_mailing_launch_recipients
from marketing.services.simple_mailing.seller_picker import (
    first_n_seller_ids,
    list_seller_picker_rows,
    seller_is_marketing_send_eligible,
    validate_selected_seller_ids,
)
from marketing.tests.test_marketing_audiences import grant_marketing_permission, next_phone
from marketing.tests.test_seller_marketing_consent import grant_seller_consent


class SimpleMailingSellerPickerTests(TestCase):
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
        }
        defaults.update(kwargs)
        return Seller.objects.create(**defaults)

    def _ids(self, brands, **kwargs):
        return {
            row.seller_id
            for row in list_seller_picker_rows(
                all_brands=False,
                brands=brands,
                **kwargs,
            )
        }

    def test_toyota_filter_includes_explicit_toyota_seller(self):
        toyota_seller = self._seller(name='Toyota Shop', brand='Toyota', brand_fk=self.toyota)
        self._seller(name='BMW Shop', brand='BMW', brand_fk=self.bmw)
        self.assertIn(toyota_seller.pk, self._ids(['Toyota']))

    def test_toyota_filter_includes_all_brands_seller(self):
        all_brands_seller = self._seller(name='All brands', all_brands=True)
        self.assertIn(all_brands_seller.pk, self._ids(['Toyota']))

    def test_other_brand_without_all_brands_is_excluded(self):
        other = self._seller(name='BMW Shop', brand='BMW', brand_fk=self.bmw)
        self.assertNotIn(other.pk, self._ids(['Toyota']))

    def test_inactive_seller_is_excluded(self):
        seller = self._seller(brand='Toyota', is_active=False)
        self.assertNotIn(seller.pk, self._ids(['Toyota']))

    def test_test_seller_is_excluded(self):
        seller = self._seller(brand='Toyota', is_test_seller=True)
        self.assertNotIn(seller.pk, self._ids(['Toyota']))

    def test_paused_seller_is_excluded(self):
        seller = self._seller(brand='Toyota', is_paused=True)
        self.assertNotIn(seller.pk, self._ids(['Toyota']))

    def test_not_receiving_requests_is_excluded(self):
        seller = self._seller(brand='Toyota', receive_requests=False)
        self.assertNotIn(seller.pk, self._ids(['Toyota']))

    def test_select_first_five_keeps_first_five_sellers(self):
        sellers = [self._seller(name=f'Shop {index}', brand='Toyota') for index in range(7)]
        rows = list_seller_picker_rows(all_brands=False, brands=['Toyota'])
        selected = first_n_seller_ids(rows, n=SELLER_SELECT_FIRST_N)
        self.assertEqual(selected, [seller.pk for seller in sellers[:5]])

    def test_select_first_five_uses_available_when_fewer(self):
        sellers = [self._seller(name=f'Shop {index}', brand='Toyota') for index in range(3)]
        rows = list_seller_picker_rows(all_brands=False, brands=['Toyota'])
        selected = first_n_seller_ids(rows, n=SELLER_SELECT_FIRST_N)
        self.assertEqual(selected, [seller.pk for seller in sellers])

    def test_selected_seller_ids_are_revalidated(self):
        valid = self._seller(name='Valid', brand='Toyota')
        validated = validate_selected_seller_ids(
            [valid.pk],
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(validated, [valid.pk])

    def test_foreign_seller_id_outside_filter_is_rejected(self):
        toyota = self._seller(name='Toyota Shop', brand='Toyota')
        bmw = self._seller(name='BMW Shop', brand='BMW', brand_fk=self.bmw)
        with self.assertRaises(SimpleMailingValidationError):
            validate_selected_seller_ids(
                [toyota.pk, bmw.pk],
                all_brands=False,
                brands=['Toyota'],
            )

    def test_consent_not_recorded_can_be_selected_but_stays_ineligible(self):
        seller = self._seller(name='No consent', brand='Toyota')
        rows = list_seller_picker_rows(all_brands=False, brands=['Toyota'])
        self.assertEqual(rows[0].seller_id, seller.pk)
        self.assertEqual(rows[0].consent_label, 'Не подтверждено')
        validated = validate_selected_seller_ids(
            [seller.pk],
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(validated, [seller.pk])
        self.assertFalse(seller_is_marketing_send_eligible(seller, rows[0].consent_status))

    def test_revoked_can_be_selected_but_stays_ineligible(self):
        seller = self._seller(name='Revoked', brand='Toyota')
        grant_seller_consent(seller, CONTACT_CONSENT_STATUS_REVOKED)
        rows = list_seller_picker_rows(all_brands=False, brands=['Toyota'])
        self.assertEqual(rows[0].consent_label, 'Отключено')
        validate_selected_seller_ids([seller.pk], all_brands=False, brands=['Toyota'])
        self.assertFalse(seller_is_marketing_send_eligible(seller, rows[0].consent_status))

    def test_granted_stays_eligible_with_operational_flags(self):
        seller = self._seller(name='Granted', brand='Toyota')
        grant_seller_consent(seller, CONTACT_CONSENT_STATUS_GRANTED)
        rows = list_seller_picker_rows(all_brands=False, brands=['Toyota'])
        self.assertEqual(rows[0].consent_label, 'Подтверждено')
        self.assertTrue(seller_is_marketing_send_eligible(seller, rows[0].consent_status))


class SimpleMailingSellerPickerViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer-sellers', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer-sellers', password='secret')
        self.url = reverse('marketing:new_mailing')
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

    def _preview(self, **extra):
        payload = {
            'action': 'preview',
            'recipient_type': RECIPIENT_TYPE_SELLERS,
            'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            'brands': ['Toyota'],
        }
        payload.update(extra)
        return self.client.post(self.url, payload)

    def test_preview_shows_seller_table_and_copy_link_for_unconfirmed(self):
        seller = self._seller(name='Open Shop')
        response = self._preview()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Показать продавцов')
        self.assertContains(response, 'Open Shop')
        self.assertContains(response, seller.whatsapp)
        self.assertContains(response, 'Не подтверждено')
        self.assertContains(response, 'Скопировать ссылку подтверждения')
        self.assertContains(response, 'Подготовить выбранных')

    def test_prepare_selected_saves_first_five_seller_ids(self):
        sellers = [self._seller(name=f'Shop {index}') for index in range(7)]
        self._preview()
        first_five = [seller.pk for seller in sellers[:5]]
        response = self.client.post(
            self.url,
            {
                'action': 'prepare_selected',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
                'brands': ['Toyota'],
                'seller_ids': [str(pk) for pk in first_five],
            },
        )
        self.assertEqual(response.status_code, 302)
        draft = load_simple_mailing_draft(self.client.session)
        self.assertEqual(draft['selected_seller_ids'], first_five)
        self.assertEqual(draft['ordinary_count'], 5)
        self.assertEqual(
            draft['selected_seller_keys'],
            [f'seller:{pk}' for pk in first_five],
        )

    def test_prepare_rejects_seller_outside_current_filter(self):
        toyota = self._seller(name='Toyota Shop')
        bmw = self._seller(name='BMW Shop', brand='BMW', brand_fk=self.bmw)
        self._preview()
        response = self.client.post(
            self.url,
            {
                'action': 'prepare_selected',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
                'brands': ['Toyota'],
                'seller_ids': [str(toyota.pk), str(bmw.pk)],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не входят в текущую аудиторию')
        self.assertIsNone(load_simple_mailing_draft(self.client.session))

    def test_repeat_preview_keeps_selected_sellers_checked(self):
        seller = self._seller(name='Kept Shop')
        self._preview()
        self.client.post(
            self.url,
            {
                'action': 'prepare_selected',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
                'brands': ['Toyota'],
                'seller_ids': [str(seller.pk)],
            },
        )
        preview = self._preview()
        self.assertContains(preview, f'value="{seller.pk}"')
        self.assertContains(preview, 'checked')

    def test_message_page_lists_selected_sellers(self):
        seller = self._seller(name='Pilot Shop', whatsapp=next_phone())
        self._preview()
        self.client.post(
            self.url,
            {
                'action': 'prepare_selected',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
                'brands': ['Toyota'],
                'seller_ids': [str(seller.pk)],
            },
        )
        response = self.client.get(reverse('marketing:new_mailing_message'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 продавцов')
        self.assertContains(response, 'Pilot Shop')
        self.assertContains(response, seller.whatsapp)
        self.assertContains(response, 'Не подтверждено')

    def test_launch_recipients_keep_only_validated_selected_sellers(self):
        first = self._seller(name='First')
        second = self._seller(name='Second')
        self._seller(name='Third')
        rows = resolve_simple_mailing_launch_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            all_brands=False,
            brands=['Toyota'],
            selected_seller_ids=[first.pk, second.pk],
        )
        ordinary = [row for row in rows if not row.is_control_recipient]
        phones = {row.phone_normalized for row in ordinary}
        self.assertEqual(phones, {first.whatsapp, second.whatsapp})
