from __future__ import annotations

from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Brand, Country, Request, Seller
from core.services.buyer_contact_service import rebuild_buyer_contact
from marketing.models import MarketingAudience, MarketingCampaign, MarketingCampaignSendRun
from marketing.services.simple_mailing import (
    MARKETPLACE_BRAND_FILTER_AVAILABLE,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
    RECIPIENT_TYPE_SELLERS,
    resolve_simple_mailing_recipients,
    validate_brand_selection,
)
from core.services.buyer_contact_utils import normalize_buyer_text
from marketing.services.simple_mailing.brands import (
    SimpleMailingValidationError,
    get_available_brands,
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


class SimpleMailingViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.url = reverse('marketing:new_mailing')

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
            response = self.client.post(
                self.url,
                {
                    'action': 'continue',
                    'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                    'all_brands': '1',
                },
            )
        self.assertEqual(send_mock.call_count, 0)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 0)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/marketing/new-mailing/message/', response.url)

    def test_message_page_shows_summary(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        self.client.post(
            self.url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'brands': ['Toyota'],
            },
        )
        response = self.client.get(reverse('marketing:new_mailing_message'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Группа получателей подготовлена')
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
