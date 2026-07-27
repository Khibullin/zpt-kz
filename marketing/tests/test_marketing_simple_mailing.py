from __future__ import annotations

from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Brand, Country, Request, Seller
from core.services.buyer_contact_service import rebuild_buyer_contact
from marketing.models import (
    MarketingAudience,
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignSendRun,
    MarketingWhatsAppTemplate,
)
from marketing.services.campaigns.constants import (
    PURPOSE_COMBINED_SELLERS,
    PURPOSE_MARKETPLACE_BUYERS,
    PURPOSE_PARTS_BUYERS,
    PURPOSE_REQUEST_SELLERS,
)
from marketing.services.campaigns.send_constants import (
    FORBIDDEN_SAMPLE_ACCESS_TOKEN,
    VARIABLE_KEY_REQUEST_HISTORY_URL,
)
from marketing.services.simple_mailing import (
    MARKETPLACE_BRAND_FILTER_AVAILABLE,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
    RECIPIENT_TYPE_SELLERS,
    load_simple_mailing_draft,
    recipient_type_to_campaign_purpose,
    render_simple_mailing_template_preview,
    resolve_simple_mailing_recipients,
)
from marketing.services.templates.constants import (
    META_STATUS_APPROVED,
    META_STATUS_DRAFT,
    META_STATUS_PENDING,
)
from marketing.tests.test_marketing_templates import make_template
from core.services.buyer_contact_utils import normalize_buyer_text
from marketing.services.simple_mailing.brands import (
    SimpleMailingValidationError,
    get_available_brands,
    is_test_brand_value,
    normalize_brand_selection,
    validate_brand_selection,
)
from marketing.tests.test_marketing_audiences import grant_marketing_permission, make_buyer, next_phone


def make_request(buyer, *, brand: str, model: str = 'Camry') -> Request:
    req = Request.objects.create(
        buyer_contact=buyer,
        phone=buyer.phone_normalized,
        transport_type='car',
        brand=brand,
        model=model,
        status='sent',
    )
    rebuild_buyer_contact(buyer)
    return req


class SimpleMailingPartsRequestBuyersTests(TestCase):
    def test_all_brands_unique_request_buyers(self):
        make_request(make_buyer(), brand='Toyota')
        make_request(make_buyer(), brand='BMW')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            all_brands=True,
        )
        self.assertEqual(result.count, 2)

    def test_one_brand(self):
        toyota = make_buyer()
        make_request(toyota, brand='Toyota')
        make_request(make_buyer(), brand='BMW')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(result.count, 1)

    def test_multiple_brands_or(self):
        toyota = make_buyer()
        bmw = make_buyer()
        make_request(make_buyer(), brand='Audi')
        make_request(toyota, brand='Toyota')
        make_request(bmw, brand='BMW')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            all_brands=False,
            brands=['Toyota', 'BMW'],
        )
        self.assertEqual(result.count, 2)

    def test_duplicate_requests_same_buyer_one_recipient(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        make_request(buyer, brand='Toyota', model='RAV4')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(result.count, 1)

    def test_buyer_with_two_selected_brands_one_recipient(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        make_request(buyer, brand='BMW')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            all_brands=False,
            brands=['Toyota', 'BMW'],
        )
        self.assertEqual(result.count, 1)

    def test_unselected_brand_excluded(self):
        make_request(make_buyer(), brand='Toyota')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            all_brands=False,
            brands=['BMW'],
        )
        self.assertEqual(result.count, 0)

    def test_test_contact_excluded(self):
        make_request(make_buyer(is_test_contact=True), brand='Toyota')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            all_brands=True,
        )
        self.assertEqual(result.count, 0)

    def test_test_brand_excluded_from_all_brands_count(self):
        make_request(make_buyer(), brand='TestBrand')
        make_request(make_buyer(), brand='Toyota')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            all_brands=True,
        )
        self.assertEqual(result.count, 1)


class SimpleMailingSellerTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='Japan')
        self.toyota_brand = Brand.objects.create(country=self.country, name='Toyota')
        self.lexus_brand = Brand.objects.create(country=self.country, name='Lexus')

    def _seller(self, **kwargs) -> Seller:
        defaults = {
            'name': 'Seller',
            'whatsapp': next_phone(),
            'transport_type': 'car',
            'city': 'Алматы',
            'is_active': True,
            'is_test_seller': False,
            'is_paused': False,
        }
        defaults.update(kwargs)
        return Seller.objects.create(**defaults)

    def test_all_sellers(self):
        self._seller(brand='Toyota')
        self._seller(brand='BMW', whatsapp=next_phone())
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            all_brands=True,
        )
        self.assertEqual(result.count, 2)

    def test_one_brand(self):
        self._seller(brand='Toyota')
        self._seller(brand='BMW', whatsapp=next_phone())
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(result.count, 1)

    def test_multiple_brands_or(self):
        self._seller(brand='Toyota')
        self._seller(brand='Lexus', brand_fk=self.lexus_brand, whatsapp=next_phone())
        self._seller(brand='BMW', whatsapp=next_phone())
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            all_brands=False,
            brands=['Toyota', 'Lexus'],
        )
        self.assertEqual(result.count, 2)

    def test_seller_with_multiple_selected_brands_one_recipient(self):
        seller = self._seller(brand='Toyota', brand_fk=self.toyota_brand)
        seller.selected_brands.add(self.lexus_brand)
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            all_brands=False,
            brands=['Toyota', 'Lexus'],
        )
        self.assertEqual(result.count, 1)

    def test_all_brands_seller_matches_any_selected_brand(self):
        self._seller(all_brands=True)
        self._seller(brand='Toyota', whatsapp=next_phone())
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(result.count, 2)

    def test_two_sellers_same_whatsapp_count_once(self):
        phone = next_phone()
        self._seller(brand='Toyota', whatsapp=phone)
        self._seller(brand='Toyota', whatsapp=phone)
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(result.count, 1)

    def test_two_sellers_different_format_same_normalized_phone(self):
        phone_key = next_phone()
        self._seller(brand='Toyota', whatsapp=f'8{phone_key[1:]}')
        self._seller(brand='Toyota', whatsapp=f'+7 {phone_key[1:4]} {phone_key[4:7]} {phone_key[7:9]} {phone_key[9:]}')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(result.count, 1)

    def test_all_brands_and_branded_seller_same_phone_count_once(self):
        phone = next_phone()
        self._seller(all_brands=True, whatsapp=phone)
        self._seller(brand='Toyota', whatsapp=phone)
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_SELLERS,
            all_brands=False,
            brands=['Toyota'],
        )
        self.assertEqual(result.count, 1)


class SimpleMailingMarketplaceTests(TestCase):
    def test_all_marketplace_buyers_paid_only(self):
        from orders.models import Order

        Order.objects.create(
            customer_name='Buyer',
            customer_phone='77001112233',
            status=Order.STATUS_PAID,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
        )
        Order.objects.create(
            customer_name='Pending',
            customer_phone='77002223344',
            status=Order.STATUS_NEW,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
        )
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_MARKETPLACE_BUYERS,
            all_brands=True,
        )
        self.assertEqual(result.count, 1)

    def test_marketplace_brand_filter_disabled(self):
        with self.assertRaises(SimpleMailingValidationError):
            validate_brand_selection(
                recipient_type=RECIPIENT_TYPE_MARKETPLACE_BUYERS,
                all_brands=False,
                brands=['Toyota'],
            )

    def test_marketplace_audit_flag(self):
        self.assertFalse(MARKETPLACE_BRAND_FILTER_AVAILABLE)


class SimpleMailingValidationTests(TestCase):
    def test_no_brand_selection_error(self):
        with self.assertRaises(SimpleMailingValidationError):
            validate_brand_selection(
                recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                all_brands=False,
                brands=[],
            )

    def test_invalid_brand_rejected(self):
        with self.assertRaises(SimpleMailingValidationError):
            validate_brand_selection(
                recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                all_brands=False,
                brands=['NonexistentBrand'],
            )

    def test_test_brand_rejected(self):
        make_request(make_buyer(), brand='Toyota')
        with self.assertRaises(SimpleMailingValidationError):
            validate_brand_selection(
                recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                all_brands=False,
                brands=['TestBrand'],
            )

    def test_all_brands_clears_concrete_brands(self):
        all_brands, brands = normalize_brand_selection(
            all_brands=True,
            brands=['Toyota', 'BMW'],
        )
        self.assertTrue(all_brands)
        self.assertEqual(brands, [])


class SimpleMailingViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.url = reverse('marketing:new_mailing')

    def _preview_then_continue(self, payload):
        preview_response = self.client.post(self.url, {**payload, 'action': 'preview'})
        self.assertEqual(preview_response.status_code, 200)
        return self.client.post(self.url, {**payload, 'action': 'continue'})

    def test_get_initial_continue_disabled(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="continue-button"')
        self.assertContains(response, 'disabled')

    def test_get_does_not_create_entities(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MarketingAudience.objects.count(), 0)
        self.assertEqual(MarketingCampaign.objects.count(), 0)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 0)

    def test_continue_does_not_send_or_create_sendrun(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        with mock.patch('core.whatsapp_template_sender.send_whatsapp_template_message') as send_mock:
            response = self._preview_then_continue(
                {
                    'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                    'all_brands': '1',
                },
            )
        self.assertEqual(send_mock.call_count, 0)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 0)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/marketing/new-mailing/message/', response.url)

    def test_continue_without_preview_rejected(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        response = self.client.post(
            self.url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'all_brands': '1',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Показать количество')

    def test_continue_with_zero_count_rejected(self):
        make_request(make_buyer(), brand='TestBrand')
        self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'all_brands': '1',
            },
        )
        response = self.client.post(
            self.url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'all_brands': '1',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Получатели не найдены')

    def test_continue_after_brand_change_rejected(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        make_request(make_buyer(), brand='BMW')
        self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': ['Toyota'],
            },
        )
        response = self.client.post(
            self.url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': ['Toyota', 'BMW'],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Фильтры изменились')

    def test_fake_client_count_ignored(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': ['Toyota'],
            },
        )
        response = self.client.post(
            self.url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': ['Toyota'],
                'count': '999',
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_message_page_shows_summary(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        self._preview_then_continue(
            {
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': ['Toyota'],
            },
        )
        response = self.client.get(reverse('marketing:new_mailing_message'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '2. Выберите сообщение')
        self.assertContains(response, 'Toyota')

    def test_unauthorized_blocked(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_nav_contains_new_mailing_section(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Новая рассылка')
        self.assertContains(response, 'Расширенные настройки')

    def test_no_recipient_type_validation(self):
        response = self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': '',
                'all_brands': '1',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Выберите тип получателей')

    @mock.patch('core.whatsapp_template_sender.send_whatsapp_template_message')
    def test_meta_mock_call_count_zero(self, send_mock):
        self.client.get(self.url)
        self.assertEqual(send_mock.call_count, 0)


class SimpleMailingBrandListTests(TestCase):
    def test_parts_request_brands_from_requests(self):
        make_request(make_buyer(), brand='Toyota')
        brands = get_available_brands(RECIPIENT_TYPE_PARTS_REQUEST_BUYERS)
        self.assertIn('Toyota', brands)

    def test_parts_request_brands_deduped_by_case_and_whitespace(self):
        make_request(make_buyer(), brand='BMW')
        make_request(make_buyer(), brand='bmw')
        make_request(make_buyer(), brand=' BMW ')
        brands = get_available_brands(RECIPIENT_TYPE_PARTS_REQUEST_BUYERS)
        bmw_variants = [brand for brand in brands if normalize_buyer_text(brand) == normalize_buyer_text('BMW')]
        self.assertEqual(len(bmw_variants), 1)

    def test_seller_brands_deduped_by_case_and_whitespace(self):
        Seller.objects.create(
            name='Seller 1',
            whatsapp=next_phone(),
            transport_type='car',
            city='Алматы',
            is_active=True,
            brand='BMW',
        )
        Seller.objects.create(
            name='Seller 2',
            whatsapp=next_phone(),
            transport_type='car',
            city='Алматы',
            is_active=True,
            brand='bmw',
        )
        Seller.objects.create(
            name='Seller 3',
            whatsapp=next_phone(),
            transport_type='car',
            city='Алматы',
            is_active=True,
            brand=' BMW ',
        )
        brands = get_available_brands(RECIPIENT_TYPE_SELLERS)
        bmw_variants = [brand for brand in brands if normalize_buyer_text(brand) == normalize_buyer_text('BMW')]
        self.assertEqual(len(bmw_variants), 1)

    def test_test_brand_absent_from_parts_buyer_brand_list(self):
        make_request(make_buyer(), brand='TestBrand')
        make_request(make_buyer(), brand='Toyota')
        brands = get_available_brands(RECIPIENT_TYPE_PARTS_REQUEST_BUYERS)
        self.assertNotIn('TestBrand', brands)
        self.assertIn('Toyota', brands)
        self.assertTrue(is_test_brand_value('TestBrand'))

    def test_test_brand_absent_from_seller_brand_list(self):
        Seller.objects.create(
            name='Seller',
            whatsapp=next_phone(),
            transport_type='car',
            city='Алматы',
            is_active=True,
            brand='TestBrand',
        )
        Seller.objects.create(
            name='Real seller',
            whatsapp=next_phone(),
            transport_type='car',
            city='Алматы',
            is_active=True,
            brand='Toyota',
        )
        brands = get_available_brands(RECIPIENT_TYPE_SELLERS)
        self.assertNotIn('TestBrand', brands)
        self.assertIn('Toyota', brands)

    def test_manipulated_post_test_brand_rejected(self):
        make_request(make_buyer(), brand='Toyota')
        client = Client()
        user = User.objects.create_user('marketer2', password='secret', is_staff=True)
        grant_marketing_permission(user)
        client.login(username='marketer2', password='secret')
        response = client.post(
            reverse('marketing:new_mailing'),
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': ['TestBrand'],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Недопустимая марка')


class SimpleMailingBrandSearchTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer-search', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer-search', password='secret')
        self.url = reverse('marketing:new_mailing')

    def test_brand_cards_include_search_data_attribute(self):
        make_request(make_buyer(), brand='Haval')
        make_request(make_buyer(), brand='Mercedes-Benz')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-brand-search="haval"')
        self.assertContains(response, 'data-brand-search="mercedes-benz"')
        self.assertContains(response, 'id="brand-search"')
        self.assertContains(response, 'filterBrandCards')
        self.assertContains(response, 'is-search-hidden')

    def test_haval_present_in_brand_list(self):
        make_request(make_buyer(), brand='Haval')
        brands = get_available_brands(RECIPIENT_TYPE_PARTS_REQUEST_BUYERS)
        self.assertIn('Haval', brands)

    def test_marketplace_does_not_render_brand_search(self):
        response = self.client.get(
            self.url,
            {'recipient_type': RECIPIENT_TYPE_MARKETPLACE_BUYERS},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="brand-search"')
        self.assertContains(response, 'Для покупателей Marketplace сейчас доступна рассылка по всем маркам.')

    def test_search_text_not_used_as_recipient_filter(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        make_request(make_buyer(), brand='BMW')
        preview = self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': ['Toyota'],
                'brand_search': 'BMW',
            },
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, 'id="recipient-count-display">1<')
        continue_response = self.client.post(
            self.url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': ['Toyota'],
                'brand_search': 'BMW',
            },
        )
        self.assertEqual(continue_response.status_code, 302)
        message = self.client.get(reverse('marketing:new_mailing_message'))
        self.assertContains(message, 'Toyota')
        self.assertContains(message, '1')

    def test_brand_search_empty_message_rendered(self):
        make_request(make_buyer(), brand='Toyota')
        response = self.client.get(self.url)
        self.assertContains(response, 'id="brand-search-empty"')
        self.assertContains(response, 'Марка не найдена')


class SimpleMailingTemplateSelectionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer-template', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer-template', password='secret')
        self.new_mailing_url = reverse('marketing:new_mailing')
        self.message_url = reverse('marketing:new_mailing_message')
        self.confirm_url = reverse('marketing:new_mailing_confirm')

    def _prepare_parts_buyers_draft(self, *, brand: str = 'Toyota') -> None:
        buyer = make_buyer()
        make_request(buyer, brand=brand)
        preview = self.client.post(
            self.new_mailing_url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': [brand],
            },
        )
        self.assertEqual(preview.status_code, 200)
        continue_response = self.client.post(
            self.new_mailing_url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': [brand],
            },
        )
        self.assertEqual(continue_response.status_code, 302)

    def _make_parts_buyer_template(self, **kwargs) -> MarketingWhatsAppTemplate:
        defaults = {
            'name': 'Информация о возможностях ZPT.KZ для покупателей',
            'meta_template_name': 'zpt_buyer_platform_info',
            'allowed_purposes': [PURPOSE_PARTS_BUYERS],
            'body_text': (
                'Уважаемый покупатель!\n'
                'Спасибо, что воспользовались ZPT.KZ.\n'
                'История заявок: {{request_history_url}}'
            ),
            'variables': [{
                'key': VARIABLE_KEY_REQUEST_HISTORY_URL,
                'label': 'История заявок',
                'required': True,
                'example': 'https://zpt.kz/my-requests/example/',
            }],
        }
        defaults.update(kwargs)
        return make_template(self.user, **defaults)

    def test_message_without_recipient_draft_redirects(self):
        response = self.client.get(self.message_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.new_mailing_url)

    def test_compatible_active_approved_template_displayed(self):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        response = self.client.get(self.message_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, template.name)
        self.assertContains(response, 'Уважаемый покупатель!')

    def test_inactive_template_not_displayed(self):
        template = self._make_parts_buyer_template(is_active=False)
        self._prepare_parts_buyers_draft()
        response = self.client.get(self.message_url)
        self.assertNotContains(response, template.name)

    def test_non_approved_template_not_displayed(self):
        template = self._make_parts_buyer_template(meta_status=META_STATUS_PENDING)
        self._prepare_parts_buyers_draft()
        response = self.client.get(self.message_url)
        self.assertNotContains(response, template.name)

    def test_incompatible_purpose_template_not_displayed(self):
        template = make_template(
            self.user,
            name='Seller outreach',
            allowed_purposes=[PURPOSE_REQUEST_SELLERS],
        )
        self._prepare_parts_buyers_draft()
        response = self.client.get(self.message_url)
        self.assertNotContains(response, template.name)

    def test_reserved_service_template_not_displayed(self):
        template = MarketingWhatsAppTemplate(
            name='Service receipt',
            meta_template_name='zpt_buyer_request_receipt',
            language_code='ru',
            meta_status=META_STATUS_APPROVED,
            is_active=True,
            allowed_purposes=[PURPOSE_PARTS_BUYERS],
            body_text='Service body',
            created_by=self.user,
        )
        MarketingWhatsAppTemplate.objects.bulk_create([template])
        template = MarketingWhatsAppTemplate.objects.get(name='Service receipt')
        self._prepare_parts_buyers_draft()
        response = self.client.get(self.message_url)
        self.assertNotContains(response, template.name)

    def test_manipulated_incompatible_template_id_rejected(self):
        seller_template = make_template(
            self.user,
            name='Seller only template',
            allowed_purposes=[PURPOSE_REQUEST_SELLERS],
        )
        self._prepare_parts_buyers_draft()
        response = self.client.post(
            self.message_url,
            {'template_id': seller_template.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.message_url)
        draft = load_simple_mailing_draft(self.client.session)
        self.assertNotIn('template_id', draft or {})

    def test_valid_template_selection_saves_template_id(self):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        response = self.client.post(
            self.message_url,
            {'template_id': template.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.confirm_url)
        draft = load_simple_mailing_draft(self.client.session)
        self.assertEqual(draft['template_id'], template.pk)
        self.assertEqual(draft['count'], 1)
        self.assertEqual(draft['brands'], ['Toyota'])

    def test_template_body_not_used_from_post(self):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        self.client.post(
            self.message_url,
            {
                'template_id': template.pk,
                'body_text': 'Injected body from POST',
            },
        )
        template.body_text = 'Updated body in database'
        template.save(update_fields=['body_text'])
        response = self.client.get(self.confirm_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Updated body in database')
        self.assertNotContains(response, 'Injected body from POST')

    def test_confirm_rereads_template_from_db(self):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        self.client.post(self.message_url, {'template_id': template.pk})
        template.name = 'Renamed template title'
        template.save(update_fields=['name'])
        response = self.client.get(self.confirm_url)
        self.assertContains(response, 'Renamed template title')

    def test_template_becoming_inactive_before_confirm_rejected(self):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        self.client.post(self.message_url, {'template_id': template.pk})
        template.is_active = False
        template.save(update_fields=['is_active'])
        response = self.client.get(self.confirm_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.message_url)

    def test_template_losing_approved_before_confirm_rejected(self):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        self.client.post(self.message_url, {'template_id': template.pk})
        template.meta_status = META_STATUS_DRAFT
        template.save(update_fields=['meta_status'])
        response = self.client.get(self.confirm_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.message_url)

    def test_no_compatible_templates_empty_state(self):
        self._prepare_parts_buyers_draft()
        response = self.client.get(self.message_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Для выбранной группы пока нет доступных WhatsApp-шаблонов.')
        self.assertContains(response, reverse('marketing:templates'))
        self.assertContains(response, 'id="continue-to-confirm-button"', count=0)

    def test_continue_disabled_without_selection_render_contract(self):
        self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        response = self.client.get(self.message_url)
        self.assertContains(response, 'id="continue-to-confirm-button"')
        self.assertContains(response, 'disabled')

    def test_zpt_buyer_platform_info_compatible_with_parts_buyers(self):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        response = self.client.get(self.message_url)
        self.assertContains(response, template.name)
        self.assertContains(response, 'zpt_buyer_platform_info', count=0)

    def test_variables_preview_safe(self):
        template = self._make_parts_buyer_template()
        preview = render_simple_mailing_template_preview(template)
        self.assertIn('https://zpt.kz/my-requests/example/', preview['body'])

    def test_fake_uuid_not_shown_as_real_recipient_url(self):
        template = self._make_parts_buyer_template(
            variables=[{
                'key': VARIABLE_KEY_REQUEST_HISTORY_URL,
                'label': 'История заявок',
                'required': True,
                'example': f'https://zpt.kz/my-requests/{FORBIDDEN_SAMPLE_ACCESS_TOKEN}/',
            }],
        )
        preview = render_simple_mailing_template_preview(template)
        self.assertIn('[Персональная ссылка на историю заявок]', preview['body'])
        self.assertNotIn(FORBIDDEN_SAMPLE_ACCESS_TOKEN, preview['body'])

    def test_get_post_do_not_create_campaign_entities(self):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        self.client.get(self.message_url)
        self.client.post(self.message_url, {'template_id': template.pk})
        self.client.get(self.confirm_url)
        self.assertEqual(MarketingAudience.objects.count(), 0)
        self.assertEqual(MarketingCampaign.objects.count(), 0)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 0)
        self.assertEqual(MarketingCampaignMessage.objects.count(), 0)

    @mock.patch('core.whatsapp_template_sender.send_whatsapp_template_message')
    def test_meta_mock_call_count_zero(self, send_mock):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        self.client.get(self.message_url)
        self.client.post(self.message_url, {'template_id': template.pk})
        self.client.get(self.confirm_url)
        self.assertEqual(send_mock.call_count, 0)

    def test_recipient_draft_count_preserved(self):
        template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        self.client.post(self.message_url, {'template_id': template.pk})
        draft = load_simple_mailing_draft(self.client.session)
        self.assertEqual(draft['count'], 1)
        self.assertEqual(draft['recipient_type'], RECIPIENT_TYPE_PARTS_REQUEST_BUYERS)

    def test_seller_page_without_seller_template_does_not_crash(self):
        Seller.objects.create(
            name='Seller',
            whatsapp=next_phone(),
            transport_type='car',
            city='Алматы',
            is_active=True,
            brand='Toyota',
        )
        preview = self.client.post(
            self.new_mailing_url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'all_brands': '1',
            },
        )
        self.assertEqual(preview.status_code, 200)
        self.client.post(
            self.new_mailing_url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'all_brands': '1',
            },
        )
        response = self.client.get(self.message_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Для выбранной группы пока нет доступных WhatsApp-шаблонов.')

    def _set_draft(self, draft: dict) -> None:
        session = self.client.session
        session['marketing_simple_mailing_draft'] = draft
        session.save()

    def test_marketplace_page_without_compatible_template_does_not_crash(self):
        make_template(
            self.user,
            name='Parts buyers only',
            allowed_purposes=[PURPOSE_PARTS_BUYERS],
        )
        self._set_draft({
            'recipient_type': RECIPIENT_TYPE_MARKETPLACE_BUYERS,
            'all_brands': True,
            'brands': [],
            'count': 3,
        })
        response = self.client.get(self.message_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Для выбранной группы пока нет доступных WhatsApp-шаблонов.')

    def test_confirm_without_template_redirects_to_message(self):
        self._prepare_parts_buyers_draft()
        response = self.client.get(self.confirm_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.message_url)

    def test_sellers_maps_to_request_sellers_purpose_only(self):
        self.assertEqual(
            recipient_type_to_campaign_purpose(RECIPIENT_TYPE_SELLERS),
            PURPOSE_REQUEST_SELLERS,
        )

    def test_combined_sellers_only_template_not_shown_for_sellers(self):
        combined_template = make_template(
            self.user,
            name='Combined sellers outreach',
            allowed_purposes=[PURPOSE_COMBINED_SELLERS],
        )
        request_sellers_template = make_template(
            self.user,
            name='Request sellers outreach',
            meta_template_name='zpt_request_sellers_only',
            allowed_purposes=[PURPOSE_REQUEST_SELLERS],
        )
        Seller.objects.create(
            name='Seller',
            whatsapp=next_phone(),
            transport_type='car',
            city='Алматы',
            is_active=True,
            brand='Toyota',
        )
        preview = self.client.post(
            self.new_mailing_url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'all_brands': '1',
            },
        )
        self.assertEqual(preview.status_code, 200)
        continue_response = self.client.post(
            self.new_mailing_url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'all_brands': '1',
            },
        )
        self.assertEqual(continue_response.status_code, 302)
        response = self.client.get(self.message_url)
        self.assertNotContains(response, combined_template.name)
        self.assertContains(response, request_sellers_template.name)

    def test_template_id_cleared_when_recipient_draft_recreated(self):
        buyer_template = self._make_parts_buyer_template()
        self._prepare_parts_buyers_draft()
        select_response = self.client.post(
            self.message_url,
            {'template_id': buyer_template.pk},
        )
        self.assertEqual(select_response.status_code, 302)
        draft = load_simple_mailing_draft(self.client.session)
        self.assertEqual(draft['template_id'], buyer_template.pk)

        Seller.objects.create(
            name='Seller',
            whatsapp=next_phone(),
            transport_type='car',
            city='Алматы',
            is_active=True,
            brand='Toyota',
        )
        preview = self.client.post(
            self.new_mailing_url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'all_brands': '1',
            },
        )
        self.assertEqual(preview.status_code, 200)
        continue_response = self.client.post(
            self.new_mailing_url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_SELLERS,
                'all_brands': '1',
            },
        )
        self.assertEqual(continue_response.status_code, 302)
        draft = load_simple_mailing_draft(self.client.session)
        self.assertEqual(draft['recipient_type'], RECIPIENT_TYPE_SELLERS)
        self.assertNotIn('template_id', draft)

        confirm_response = self.client.get(self.confirm_url)
        self.assertEqual(confirm_response.status_code, 302)
        self.assertEqual(confirm_response.url, self.message_url)
