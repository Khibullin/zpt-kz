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
        for label in ['Учёт товаров и денег', 'Подбор товаров и закупки']:
            self.assertContains(response, label)
        for label in ['Заявки покупателей', 'Продавцы и СТО',
                      'Управление ценами Kaspi', 'Маркетинг', 'Администрирование']:
            self.assertNotContains(response, label)
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
        self.assertNotContains(response, 'Заявки покупателей')
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

    def test_shared_agparts_navigation(self):
        self.client.force_login(self.user)
        home = self.client.get(reverse('office_home'))
        self.assertContains(home, 'AG Parts — единый кабинет')
        self.assertContains(home, 'Открыть AG Parts', count=1)
        for route in ['inventory:overview', 'inventory:updates', 'inventory:purchases',
                      'procurement:supplier_price_comparison', 'procurement:basket',
                      'repricer:dashboard']:
            response = self.client.get(reverse(route))
            self.assertEqual(response.status_code, 200, route)
            self.assertContains(response, 'aria-label="AG Parts — единое меню"')
            for label in ['Сравнение цен', 'Выгодные к закупке', 'Список закупки',
                          'Учёт закупок', 'Товары и остатки', 'Сверка остатков и продаж',
                          'Контроль цен Kaspi']:
                self.assertContains(response, label)
        response = self.client.get(reverse('inventory:updates'))
        self.assertContains(response, 'href="' + reverse('inventory:updates') + '" aria-current="page"')

    def test_shared_sidebar_does_not_expand_sales_permissions(self):
        user = get_user_model().objects.create_user(username='agparts-sales', password='test-password')
        Membership.objects.create(user=user, role='sales', active=True)
        self.client.force_login(user)
        response = self.client.get(reverse('procurement:basket'))
        self.assertContains(response, 'Список закупки')
        self.assertNotContains(response, 'href="' + reverse('inventory:overview') + '"')
        self.assertNotContains(response, 'href="' + reverse('repricer:dashboard') + '"')
