import json
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from core.phone_utils import (
    build_whatsapp_url, is_excluded_city_whatsapp,
    normalize_phone_for_whatsapp, seller_whatsapp_phone, seller_whatsapp_url,
)
from core.models import Seller, SellerExtraPhone, SellerSocialLink
from core.services.seller_card_display import build_seller_card
from core.services.request_connections import directory_contact


class SellerWhatsappSelectionTests(SimpleTestCase):
    def test_city_codes_are_excluded_only_from_whatsapp(self):
        for raw in ('+7 (727) 123-45-67', '8 (727) 123-45-67', '7271234567',
                    '+7 (327) 123-45-67', '83271234567', '3271234567'):
            with self.subTest(raw=raw):
                self.assertTrue(is_excluded_city_whatsapp(raw))
                self.assertEqual(build_whatsapp_url(raw), '')
                self.assertIsNotNone(normalize_phone_for_whatsapp(raw))
        # The same digits inside a mobile number or a different country are safe.
        self.assertEqual(build_whatsapp_url('77013271234'), 'https://wa.me/77013271234')
        self.assertEqual(build_whatsapp_url('+998327123456'), 'https://wa.me/998327123456')

    def test_next_phone_is_selected_without_rewriting_primary(self):
        shop = SimpleNamespace(whatsapp='77271234567', phone2='77011234567')
        self.assertEqual(seller_whatsapp_phone(shop), '77011234567')
        self.assertEqual(shop.whatsapp, '77271234567')
        shop.whatsapp = '77021234567'
        self.assertEqual(seller_whatsapp_phone(shop), '77021234567')

    def test_service_import_contacts_are_read_in_order_only_from_contacts_column(self):
        shop = SimpleNamespace(whatsapp='77271234567', catalog_notes='IMPORT 77099999999\n' + json.dumps({
            'ID организации': '77088888888', 'Контакты': '+77271234567, +7 (747) 123-45-67, +77021234567'}))
        self.assertEqual(seller_whatsapp_phone(shop), '77471234567')
        shop.catalog_notes = 'ID организации 77088888888; unknown arbitrary text'
        self.assertEqual(seller_whatsapp_phone(shop), '')
        shop.catalog_notes = '{"Контакты": null, "ID организации": "77088888888"}'
        self.assertEqual(seller_whatsapp_url(shop), '')


class SellerWhatsappCardTests(TestCase):
    def setUp(self):
        self.shop = Seller.objects.create(name='Тестовая организация', city='Алматы',
            whatsapp='77271234567', phone2='77271234568', is_active=True,
            sells_new_parts=True, all_brands=True, all_categories=True)

    def test_skips_two_city_numbers_and_preserves_call_number(self):
        SellerExtraPhone.objects.create(seller=self.shop, phone='73271234567')
        SellerExtraPhone.objects.create(seller=self.shop, phone='77011234567')
        card = build_seller_card(self.shop)
        self.assertEqual(card['primary_phone']['tel'], 'tel:+77271234567')
        self.assertEqual(card['whatsapp_url'], 'https://wa.me/77011234567')
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.whatsapp, '77271234567')
        req = SimpleNamespace(pk=1, brand='Toyota', model='Camry', category='Двигатель')
        self.assertTrue(directory_contact(self.shop, req, 'whatsapp').startswith('https://wa.me/77011234567?text='))
        self.assertEqual(directory_contact(self.shop, req, 'call'), 'tel:+77271234567')

    def test_no_alternate_hides_whatsapp_instead_of_using_city_social_link(self):
        SellerSocialLink.objects.create(seller=self.shop, kind='whatsapp', url='https://wa.me/77271234567')
        card = build_seller_card(self.shop)
        self.assertEqual(card['whatsapp_url'], '')
        self.assertEqual(card['socials'], [])
        self.assertEqual(card['primary_phone']['tel'], 'tel:+77271234567')
        req = SimpleNamespace(pk=1, brand='Toyota', model='Camry', category='Двигатель')
        self.assertEqual(directory_contact(self.shop, req, 'whatsapp'), '')

    def test_template_filters_use_the_same_next_number(self):
        from django.template import Context, Template
        self.shop.phone2 = '77011234567'
        for library in ('phone_extras', 'product_extras'):
            html = Template('{% load ' + library + ' %}{{ shop|seller_whatsapp_url }}').render(Context({'shop': self.shop}))
            self.assertEqual(html, 'https://wa.me/77011234567')

    def test_service_card_keeps_call_but_changes_whatsapp(self):
        from service_requests.models import ServiceSeller
        from service_requests.directory_display import service_card
        shop = ServiceSeller.objects.create(name='Тестовое СТО', city='Алматы', seller_type='sto',
            whatsapp='77271234567', catalog_notes='IMPORT\n{"Контакты": "+77271234567, +77011234567"}')
        card = service_card(shop)
        self.assertEqual(card['tel'], '+77271234567')
        self.assertEqual(card['whatsapp_url'], 'https://wa.me/77011234567')
