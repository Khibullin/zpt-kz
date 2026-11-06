from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from procurement.models import Membership


class AccountingManagerAccessTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('manager')
        self.membership = Membership.objects.create(user=self.manager, role='sales')
        self.permission = Permission.objects.get(
            content_type__app_label='inventory', codename='manage_accounting',
        )

    def test_manager_with_explicit_permission_can_open_accounting(self):
        self.manager.user_permissions.add(self.permission)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get('/office/'), 'Открыть учёт')
        for route in ['overview', 'items', 'documents', 'cash', 'purchases',
                      'stock', 'sales', 'finance', 'upload', 'settlements',
                      'operating_report', 'cost_report', 'reconciliation']:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse('inventory:' + route)).status_code, 200)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.manager.refresh_from_db()
        self.membership.refresh_from_db()
        self.assertFalse(self.manager.is_staff)
        self.assertFalse(self.manager.is_superuser)
        self.assertEqual(self.membership.role, 'sales')

    def test_other_manager_still_denied_including_post(self):
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get('/office/'), 'Открыть учёт')
        self.assertEqual(self.client.get(reverse('inventory:overview')).status_code, 403)
        self.assertEqual(self.client.post(reverse('inventory:upload')).status_code, 403)
        self.assertEqual(self.client.post(reverse('inventory:document_action', args=[999])).status_code, 403)

    def test_permission_without_active_sales_role_is_insufficient(self):
        self.manager.user_permissions.add(self.permission)
        self.client.force_login(self.manager)
        for role, active in [('user', True), ('sales', False)]:
            self.membership.role, self.membership.active = role, active
            self.membership.save()
            self.assertEqual(self.client.get(reverse('inventory:items')).status_code, 403)

    def test_revocation_removes_access(self):
        self.manager.user_permissions.add(self.permission)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('inventory:items')).status_code, 200)
        self.manager.user_permissions.remove(self.permission)
        self.assertEqual(self.client.get(reverse('inventory:items')).status_code, 403)

    def test_targeted_grant_keeps_roles_and_other_users_unchanged(self):
        target = get_user_model().objects.create_user('rasidhajbullin42@gmail.com')
        Membership.objects.create(user=target, role='sales')
        migration = import_module('inventory.migrations.0010_accounting_manager_access')
        migration.grant_manager(apps, SimpleNamespace(connection=connection))
        target.refresh_from_db()
        self.assertTrue(target.has_perm('inventory.manage_accounting'))
        self.assertFalse(target.is_staff)
        self.assertFalse(target.is_superuser)
        self.assertEqual(target.membership.role, 'sales')
        self.assertFalse(self.manager.has_perm('inventory.manage_accounting'))

    def test_anonymous_redirects_to_office_login(self):
        response = self.client.get(reverse('inventory:items'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/office/login/'))
