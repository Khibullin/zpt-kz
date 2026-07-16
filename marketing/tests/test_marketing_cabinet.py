from __future__ import annotations

import os

from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BuyerContact,
    Seller,
)
from core.services.buyer_contact_utils import mask_phone
from marketing.models import MarketingCabinetPermission
from marketing.permissions import MARKETING_CABINET_PERMISSION
from orders.models import Order

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
        self.assertContains(
            response,
            'Данные появятся после начала оформления покупок через маркетплейс',
        )

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

    def test_marketplace_buyer_from_order_appears_in_tab(self):
        Order.objects.create(
            customer_name='Market buyer',
            customer_phone='+7 701 999 88 77',
            total_price=1000,
            delivery_method='pickup',
        )
        response = self.client.get(
            self.contacts_url,
            {'tab': 'marketplace_buyers'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, mask_phone('77019998877'))

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
