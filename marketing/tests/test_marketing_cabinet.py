from __future__ import annotations

import os

from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    BuyerContact,
    ContactConsent,
    Seller,
)
from core.services.buyer_contact_utils import mask_phone
from marketing.models import MarketingCabinetPermission
from marketing.permissions import MARKETING_CABINET_PERMISSION
from marketing.services.contacts import ROLE_MARKETPLACE_BUYER, build_contact_registry
from marketing.services.dashboard import get_group_cards, get_overview_stats
from marketing.services.marketplace_orders import (
    MARKETPLACE_BUYERS_EMPTY_NOTE,
    MARKETPLACE_BUYERS_FILTER_NOTE,
    MARKETPLACE_BUYERS_PAID_NOTE,
    SELLER_EXECUTOR_CONSENT_NOTE,
    audit_marketplace_orders,
    explain_marketplace_order_inclusion,
    get_marketplace_buyer_counts,
)
from orders.models import Order
from service_requests.models import ServiceSeller

_phone_counter = 9200000


def make_buyer(**kwargs) -> BuyerContact:
    global _phone_counter
    _phone_counter += 1
    defaults = {
        'phone_normalized': f'77{_phone_counter:09d}'[-11:],
        'status': BUYER_CONTACT_STATUS_ACTIVE,
    }
    defaults.update(kwargs)
    return BuyerContact.objects.create(**defaults)


def grant_marketing_permission(user: User) -> None:
    content_type = ContentType.objects.get_for_model(MarketingCabinetPermission)
    permission = Permission.objects.get(
        content_type=content_type,
        codename='access_marketing_cabinet',
    )
    user.user_permissions.add(permission)


def grant_marketing(buyer: BuyerContact) -> None:
    ContactConsent.objects.create(
        buyer=buyer,
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        status=CONTACT_CONSENT_STATUS_GRANTED,
        consented_at=timezone.now(),
    )


def create_order(**kwargs) -> Order:
    defaults = {
        'customer_name': 'Покупатель',
        'customer_phone': '+7 701 123 45 67',
        'total_price': 1000,
        'delivery_method': Order.DELIVERY_PICKUP,
    }
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


class MarketingCabinetAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.dashboard_url = reverse('marketing:dashboard')
        self.contacts_url = reverse('marketing:contacts')

        self.regular_user = User.objects.create_user(
            username='buyer_user',
            password='pass12345',
        )
        self.staff_user = User.objects.create_user(
            username='staff_user',
            password='pass12345',
            is_staff=True,
        )
        self.staff_with_perm = User.objects.create_user(
            username='staff_marketing',
            password='pass12345',
            is_staff=True,
        )
        grant_marketing_permission(self.staff_with_perm)
        self.superuser = User.objects.create_superuser(
            username='admin_marketing',
            password='pass12345',
            email='admin@example.com',
        )

    def test_regular_user_has_no_access(self):
        self.client.login(username='buyer_user', password='pass12345')
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_staff_without_permission_has_no_access(self):
        self.client.login(username='staff_user', password='pass12345')
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 403)

    def test_staff_with_permission_has_access(self):
        self.client.login(username='staff_marketing', password='pass12345')
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Рассылки и уведомления')

    def test_superuser_has_access(self):
        self.client.login(username='admin_marketing', password='pass12345')
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)


class MarketingCabinetDisplayTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='display_admin',
            password='pass12345',
            email='display@example.com',
        )
        self.client.login(username='display_admin', password='pass12345')
        self.dashboard_url = reverse('marketing:dashboard')
        self.contacts_url = reverse('marketing:contacts')

    def test_full_phone_is_not_displayed(self):
        buyer = make_buyer(phone_normalized='77011234567')
        response = self.client.get(self.contacts_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '77011234567')
        self.assertContains(response, mask_phone(buyer.phone_normalized))

    def test_test_contacts_only_on_test_tab(self):
        test_buyer = make_buyer(
            phone_normalized='77011910000',
            is_test_contact=True,
        )
        regular_buyer = make_buyer(
            phone_normalized='77015554433',
            is_test_contact=False,
        )
        response_all = self.client.get(self.contacts_url)
        self.assertContains(response_all, mask_phone(test_buyer.phone_normalized))
        self.assertContains(response_all, mask_phone(regular_buyer.phone_normalized))

        response_parts = self.client.get(
            self.contacts_url,
            {'tab': 'parts_buyers'},
        )
        self.assertNotContains(response_parts, mask_phone(test_buyer.phone_normalized))
        self.assertContains(response_parts, mask_phone(regular_buyer.phone_normalized))

        response_test = self.client.get(
            self.contacts_url,
            {'tab': 'test'},
        )
        self.assertContains(response_test, mask_phone(test_buyer.phone_normalized))
        self.assertNotContains(response_test, mask_phone(regular_buyer.phone_normalized))

    def test_single_phone_with_multiple_roles_not_duplicated_in_all_tab(self):
        phone = '77013334455'
        make_buyer(phone_normalized=phone, is_test_contact=False)
        Seller.objects.create(
            name='Parts seller',
            whatsapp=phone,
            transport_type='car',
            is_active=True,
        )
        response = self.client.get(self.contacts_url, {'tab': 'all'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode('utf-8').count(mask_phone(phone)),
            1,
        )

    def test_empty_marketplace_buyers_displayed_correctly(self):
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Покупатели товаров маркетплейса')
        self.assertContains(response, MARKETPLACE_BUYERS_EMPTY_NOTE)

    @override_settings(BUYER_BROADCAST_MODE='TEST')
    def test_broadcast_mode_test_displayed_from_environment(self):
        with self.settings(BUYER_BROADCAST_MODE='TEST'):
            os.environ['BUYER_BROADCAST_MODE'] = 'TEST'
            try:
                response = self.client.get(self.dashboard_url)
            finally:
                os.environ.pop('BUYER_BROADCAST_MODE', None)
        self.assertContains(response, 'Режим: TEST')

    def test_no_working_mass_send_button(self):
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'type="submit" name="send"')
        self.assertNotContains(response, 'Отправить рассылку')
        self.assertContains(
            response,
            'Создать кампанию — будет доступно на следующем этапе',
        )
        self.assertContains(response, 'disabled')


class MarketingContactsDataTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='contacts_admin',
            password='pass12345',
            email='contacts@example.com',
        )
        self.client.login(username='contacts_admin', password='pass12345')
        self.contacts_url = reverse('marketing:contacts')

    def test_marketplace_buyer_from_paid_order_appears_in_tab(self):
        create_order(
            customer_name='Market buyer',
            customer_phone='+7 701 999 88 77',
            status=Order.STATUS_PAID,
        )
        response = self.client.get(
            self.contacts_url,
            {'tab': 'marketplace_buyers'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, mask_phone('77019998877'))

    def test_marketplace_buyer_from_new_order_not_counted(self):
        create_order(
            customer_name='Draft buyer',
            customer_phone='+7 701 888 77 66',
            status=Order.STATUS_NEW,
        )
        response = self.client.get(
            self.contacts_url,
            {'tab': 'marketplace_buyers'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, mask_phone('77018887766'))

    def test_permission_codename_exists(self):
        self.assertTrue(
            Permission.objects.filter(
                codename='access_marketing_cabinet',
            ).exists(),
        )
        user = User.objects.create_user(
            username='perm_check',
            password='pass12345',
            is_staff=True,
        )
        grant_marketing_permission(user)
        self.assertTrue(user.has_perm(MARKETING_CABINET_PERMISSION))


class MarketingConsentAndOrderStatsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='consent_admin',
            password='pass12345',
            email='consent@example.com',
        )
        self.client.login(username='consent_admin', password='pass12345')
        self.dashboard_url = reverse('marketing:dashboard')
        self.contacts_url = reverse('marketing:contacts')

    def test_dashboard_has_no_english_marketing_consent_labels(self):
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Marketing consent')
        self.assertContains(response, 'Согласия покупателей')
        self.assertContains(response, 'Рекламное согласие дано')
        self.assertContains(response, 'Согласие не подтверждено')
        self.assertContains(response, 'Согласие отозвано')

    def test_consent_stats_section_refers_to_buyer_contacts_only(self):
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, 'Согласия покупателей')
        self.assertContains(
            response,
            'Статистика относится только к покупателям по заявкам на запчасти',
        )

    def test_test_contacts_with_granted_not_in_marketing_available(self):
        test_buyer = make_buyer(is_test_contact=True)
        grant_marketing(test_buyer)
        regular_buyer = make_buyer(is_test_contact=False)
        grant_marketing(regular_buyer)
        stats = get_overview_stats()
        self.assertEqual(stats.marketing_available, 1)

    def test_sellers_without_consent_not_shown_as_granted(self):
        Seller.objects.create(
            name='Parts seller',
            whatsapp='77014445566',
            transport_type='car',
            is_active=True,
        )
        cards = {card.key: card for card in get_group_cards()}
        self.assertEqual(cards['parts_sellers'].with_marketing_consent, 0)
        self.assertEqual(
            cards['parts_sellers'].consent_note,
            SELLER_EXECUTOR_CONSENT_NOTE,
        )

    def test_service_sellers_without_consent_not_shown_as_granted(self):
        ServiceSeller.objects.create(
            name='STO provider',
            whatsapp='77015556677',
            password='hash',
            city='Алматы',
            seller_type='sto',
            is_active=True,
        )
        ServiceSeller.objects.create(
            name='Detailing provider',
            whatsapp='77016667788',
            password='hash',
            city='Алматы',
            seller_type='detailing',
            is_active=True,
        )
        cards = {card.key: card for card in get_group_cards()}
        self.assertEqual(cards['sto'].with_marketing_consent, 0)
        self.assertEqual(cards['detailing'].with_marketing_consent, 0)
        self.assertEqual(cards['sto'].consent_note, SELLER_EXECUTOR_CONSENT_NOTE)

    def test_marketplace_buyers_count_unique_normalized_phones(self):
        create_order(
            customer_phone='87019998877',
            status=Order.STATUS_PAID,
        )
        create_order(
            customer_phone='+7 (701) 999-88-77',
            status=Order.STATUS_PAID,
        )
        cards = {card.key: card for card in get_group_cards()}
        self.assertEqual(cards['marketplace_buyers'].real_total, 1)
        self.assertEqual(cards['marketplace_buyers'].test_total, 0)

    def test_cancelled_and_new_orders_not_counted(self):
        create_order(
            customer_phone='77012223344',
            status=Order.STATUS_NEW,
        )
        create_order(
            customer_phone='77013334455',
            status=Order.STATUS_CANCELLED,
        )
        create_order(
            customer_phone='77014445566',
            status=Order.STATUS_PAID,
        )
        cards = {card.key: card for card in get_group_cards()}
        self.assertEqual(cards['marketplace_buyers'].real_total, 1)
        self.assertEqual(cards['marketplace_buyers'].test_total, 0)

    def test_order_phones_are_masked_in_contacts_html(self):
        create_order(
            customer_phone='77017778899',
            status=Order.STATUS_NEW,
        )
        create_order(
            customer_phone='77018889900',
            status=Order.STATUS_PAID,
        )
        response = self.client.get(
            self.contacts_url,
            {'tab': 'marketplace_buyers'},
        )
        content = response.content.decode('utf-8')
        self.assertNotIn('77017778899', content)
        self.assertNotIn('77018889900', content)
        self.assertIn(mask_phone('77018889900'), content)

    def test_marketplace_order_audit_explains_non_paid_orders(self):
        order = create_order(
            customer_phone='77019990011',
            status=Order.STATUS_NEW,
        )
        included_real, included_test, reason = explain_marketplace_order_inclusion(order)
        self.assertFalse(included_real)
        self.assertFalse(included_test)
        self.assertIn('незавершённая покупка', reason)
        audit_rows = audit_marketplace_orders()
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0].masked_phone, mask_phone('77019990011'))
        self.assertFalse(audit_rows[0].included_in_real_stats)
        self.assertFalse(audit_rows[0].included_in_test_stats)

    def test_non_paid_orders_show_filter_note_on_dashboard(self):
        create_order(status=Order.STATUS_NEW)
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, MARKETPLACE_BUYERS_FILTER_NOTE)


class MarketingMarketplaceBuyerSplitTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='marketplace_split_admin',
            password='pass12345',
            email='marketplace@example.com',
        )
        self.client.login(username='marketplace_split_admin', password='pass12345')
        self.dashboard_url = reverse('marketing:dashboard')

    def test_real_paid_order_counts_as_real_marketplace_buyer(self):
        create_order(
            customer_phone='77014445566',
            status=Order.STATUS_PAID,
        )
        counts = get_marketplace_buyer_counts()
        self.assertEqual(counts.real_total, 1)
        self.assertEqual(counts.test_total, 0)

    def test_test_paid_order_not_counted_as_real_marketplace_buyer(self):
        make_buyer(phone_normalized='77011910000', is_test_contact=True)
        create_order(
            customer_phone='77011910000',
            status=Order.STATUS_PAID,
        )
        counts = get_marketplace_buyer_counts()
        self.assertEqual(counts.real_total, 0)
        self.assertEqual(counts.test_total, 1)

    def test_multiple_paid_orders_same_phone_count_as_one_buyer(self):
        make_buyer(phone_normalized='77011910000', is_test_contact=True)
        for _ in range(4):
            create_order(
                customer_phone='77011910000',
                status=Order.STATUS_PAID,
            )
        counts = get_marketplace_buyer_counts()
        self.assertEqual(counts.real_total, 0)
        self.assertEqual(counts.test_total, 1)

    def test_new_orders_do_not_count_as_marketplace_buyers(self):
        make_buyer(phone_normalized='77011910000', is_test_contact=True)
        for _ in range(8):
            create_order(
                customer_phone='77011910000',
                status=Order.STATUS_NEW,
            )
        counts = get_marketplace_buyer_counts()
        self.assertEqual(counts.real_total, 0)
        self.assertEqual(counts.test_total, 0)

    def test_test_paid_buyer_keeps_role_and_test_flag_in_registry(self):
        make_buyer(phone_normalized='77011910000', is_test_contact=True)
        create_order(
            customer_phone='77011910000',
            status=Order.STATUS_PAID,
        )
        contact = build_contact_registry()['77011910000']
        self.assertIn(ROLE_MARKETPLACE_BUYER, contact.roles)
        self.assertTrue(contact.is_test)

    def test_test_paid_buyer_with_granted_not_in_marketing_available(self):
        buyer = make_buyer(phone_normalized='77011910000', is_test_contact=True)
        grant_marketing(buyer)
        create_order(
            customer_phone='77011910000',
            status=Order.STATUS_PAID,
        )
        stats = get_overview_stats()
        self.assertEqual(stats.marketing_available, 0)

    def test_dashboard_shows_real_and_test_marketplace_buyer_counts(self):
        make_buyer(phone_normalized='77011910000', is_test_contact=True)
        create_order(
            customer_phone='77011910000',
            status=Order.STATUS_PAID,
        )
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, 'Реальных покупателей товаров')
        self.assertContains(response, 'Тестовых покупателей товаров')
        self.assertContains(response, MARKETPLACE_BUYERS_PAID_NOTE)
        cards = {card.key: card for card in get_group_cards()}
        self.assertEqual(cards['marketplace_buyers'].real_total, 0)
        self.assertEqual(cards['marketplace_buyers'].test_total, 1)
