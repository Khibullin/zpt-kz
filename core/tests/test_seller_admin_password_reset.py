from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.models import Seller


class SellerAdminPasswordResetTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='admin-reset',
            email='admin-reset@example.com',
            password='AdminPass123',
        )
        self.seller = Seller.objects.create(
            name='Кабинет продавца',
            whatsapp='77001112233',
            transport_type='car',
            city='Алматы',
            password_hash=make_password('OldSellerPass1'),
            must_change_password=True,
        )
        self.auth_user = User.objects.create_user(
            username='77001112233',
            password='DjangoUserPass1',
        )
        self.url = reverse('admin_seller_reset_password', args=[self.seller.pk])
        self.change_url = reverse('admin:core_seller_change', args=[self.seller.pk])

    def _post(self, password, confirm, user=None):
        if user is None:
            user = self.superuser
        self.client.force_login(user)
        return self.client.post(
            self.url,
            {
                'new_password': password,
                'new_password_confirm': confirm,
            },
        )

    def test_superuser_sees_reset_button_on_seller_change(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.change_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Сбросить пароль продавца')
        self.assertContains(response, self.url)

    def test_get_form_ok(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Новый пароль')
        self.assertContains(response, 'Повторите новый пароль')
        self.assertContains(response, 'Сохранить новый пароль')
        self.assertContains(response, 'Отмена')
        self.assertContains(
            response,
            'Изменяется пароль кабинета продавца Seller. Пароль Django-пользователя не изменяется.',
        )
        self.assertNotContains(response, self.seller.password_hash)

    def test_short_password_rejected(self):
        old_hash = self.seller.password_hash
        response = self._post('12345', '12345')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'минимум 6')
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.password_hash, old_hash)
        self.assertTrue(self.seller.must_change_password)

    def test_mismatched_passwords_rejected(self):
        old_hash = self.seller.password_hash
        response = self._post('NewPass123', 'OtherPass123')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не совпадают')
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.password_hash, old_hash)
        self.assertTrue(check_password('OldSellerPass1', self.seller.password_hash))

    def test_valid_password_updates_seller_hash_only(self):
        old_user_hash = self.auth_user.password
        response = self._post('NewSellerPass1', 'NewSellerPass1')
        self.assertRedirects(response, self.change_url)
        self.seller.refresh_from_db()
        self.assertTrue(check_password('NewSellerPass1', self.seller.password_hash))
        self.assertFalse(check_password('OldSellerPass1', self.seller.password_hash))
        self.assertFalse(self.seller.must_change_password)
        self.auth_user.refresh_from_db()
        self.assertEqual(self.auth_user.password, old_user_hash)
        self.assertTrue(self.auth_user.check_password('DjangoUserPass1'))

    def test_user_without_change_seller_cannot_reset(self):
        staff = User.objects.create_user(
            username='staff-no-perm',
            password='StaffPass123',
            is_staff=True,
        )
        view_perm = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(Seller),
            codename='view_seller',
        )
        staff.user_permissions.add(view_perm)
        old_hash = self.seller.password_hash
        response = self._post('NewSellerPass1', 'NewSellerPass1', user=staff)
        self.assertEqual(response.status_code, 403)
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.password_hash, old_hash)
        self.assertTrue(self.seller.must_change_password)
