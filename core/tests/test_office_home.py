from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from procurement.models import Membership

class OfficeHomeTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='office-admin',password='test-password')

    def test_login_required_and_no_public_entry(self):
        response=self.client.get(reverse('office_home'))
        self.assertEqual(response.status_code,302)
        self.assertIn('/office/login/',response.url)
        self.assertEqual(self.client.get(reverse('office_login')).status_code,200)
        ordinary=get_user_model().objects.create_user(username='buyer',password='test-password')
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(reverse('office_home')).status_code,403)

    def test_admin_links_and_existing_destinations(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('office_home'))
        for label in ['Учёт товаров и денег','Подбор товаров и закупки','Заявки покупателей','Продавцы и СТО','Управление ценами Kaspi','Маркетинг','Администрирование']:
            self.assertContains(response,label)
        for route in ['inventory:overview','procurement:dashboard','control_panel:parts_request_list','control_panel:seller_list','control_panel:sto_list','repricer:dashboard','marketing:dashboard','admin:index']:
            response=self.client.get(reverse(route))
            self.assertEqual(response.status_code,200,route)
            self.assertContains(response,'Служебный кабинет')
        self.assertEqual(self.client.get(reverse('office_logout')).status_code,405)

    def test_sales_member_sees_only_authorized_sections(self):
        user=get_user_model().objects.create_user(username='sales',password='test-password')
        Membership.objects.create(user=user,role='sales',active=True)
        self.client.force_login(user)
        response=self.client.get(reverse('office_home'))
        self.assertContains(response,'Подбор товаров и закупки')
        for label in ['Учёт товаров и денег','Заявки покупателей','Маркетинг','Администрирование']:
            self.assertNotContains(response,label)
        self.assertEqual(self.client.get(reverse('inventory:overview')).status_code,403)
        self.client.logout()
        response=self.client.post(reverse('office_login'),{'username':'sales','password':'test-password'})
        self.assertRedirects(response,reverse('office_home'))

    def test_staff_without_procurement_gets_no_accounting(self):
        user=get_user_model().objects.create_user(username='staff',password='test-password',is_staff=True)
        self.client.force_login(user)
        response=self.client.get(reverse('office_home'))
        self.assertContains(response,'Заявки покупателей')
        self.assertNotContains(response,'Открыть учёт')
        self.assertNotContains(response,'Открыть подбор')
        self.assertNotContains(response,'<h2>Маркетинг</h2>')

    def test_login_disallows_buyers_and_external_next(self):
        get_user_model().objects.create_user(username='buyer',password='test-password')
        response=self.client.post(reverse('office_login'),{'username':'buyer','password':'test-password'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Доступ к служебному кабинету не выдан')
        response=self.client.post(reverse('office_login'),{'username':'office-admin','password':'test-password','next':'https://example.com/'})
        self.assertRedirects(response,reverse('office_home'))
