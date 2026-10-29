from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .invitations import digest
from .models import Activity, ManagerInvitation, Membership, Preferences


class ManagerInvitationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user('invite-admin', password='original-password')
        Membership.objects.create(user=cls.admin, role='admin')
        Preferences.objects.create(user=cls.admin, commission=Decimal('10.9'), expenses=250)

    def issue(self, email='new.manager@example.com'):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('procurement:invite_manager'), {'email': email})
        self.assertEqual(response.status_code, 200)
        return response, urlsplit(response.context['invitation_url']).path

    def accept(self, client, path, **extra):
        response = client.get(path)
        if response.status_code != 302:
            return response
        return client.post(response.url, {'new_password1': 'A-new-unique-password-2026!', 'new_password2': 'A-new-unique-password-2026!', **extra})

    def test_admin_issues_no_account_until_password_then_scoped_role_and_login(self):
        response, path = self.issue('NEW.Manager@example.com')
        invitation = ManagerInvitation.objects.get()
        self.assertEqual(invitation.email, 'new.manager@example.com')
        self.assertAlmostEqual((invitation.expires_at - timezone.now()).total_seconds(), 72*3600, delta=5)
        self.assertEqual(invitation.token_digest, digest(path.rstrip('/').split('/')[-1]))
        self.assertNotIn(path.rstrip('/').split('/')[-1], invitation.token_digest)
        self.assertFalse(get_user_model().objects.filter(email=invitation.email).exists())
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        manager = Client()
        # Preview by mail/link scanners must never consume the token or grant access.
        landing = manager.get(path)
        self.assertEqual(landing['Referrer-Policy'], 'no-referrer')
        self.assertEqual(landing.url, reverse('procurement:activate_invitation'))
        self.assertNotIn(path.rstrip('/').split('/')[-1], landing.url)
        self.assertContains(manager.get(landing.url), 'Активировать доступ')
        invitation.refresh_from_db()
        self.assertIsNone(invitation.used_at)
        accepted = self.accept(manager, path, role='admin', email='attacker@example.com')
        self.assertContains(accepted, 'Доступ активирован')
        user = get_user_model().objects.get(email=invitation.email)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(Membership.objects.get(user=user).role, 'sales')
        self.assertTrue(user.check_password('A-new-unique-password-2026!'))
        prefs = Preferences.objects.get(user=user)
        self.assertEqual(prefs.commission, Decimal('10.9'))
        self.assertEqual(prefs.expenses, Decimal('250'))
        invitation.refresh_from_db()
        self.assertEqual(invitation.accepted_by, user)
        self.assertIsNotNone(invitation.used_at)
        logged_in = manager.post(reverse('procurement:login'), {'username': 'NEW.MANAGER@example.com', 'password': 'A-new-unique-password-2026!'})
        self.assertRedirects(logged_in, reverse('procurement:dashboard'))
        self.assertEqual(manager.get(reverse('procurement:upload')).status_code, 200)
        self.assertEqual(manager.get(reverse('procurement:users')).status_code, 403)
        self.assertEqual(manager.get(reverse('procurement:invite_manager')).status_code, 403)
        self.assertNotEqual(manager.get('/admin/').status_code, 200)

    def test_replay_and_expired_and_revoked_creator_cannot_activate(self):
        _, path = self.issue()
        client = Client()
        self.accept(client, path)
        self.assertContains(self.accept(Client(), path), 'Приглашение недействительно')
        self.assertEqual(Activity.objects.filter(action='manager_invitation_accepted').count(), 1)
        _, expired = self.issue('expired@example.com')
        ManagerInvitation.objects.filter(email='expired@example.com').update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertContains(self.accept(Client(), expired), 'Приглашение недействительно')
        _, revoked = self.issue('revoked@example.com')
        Membership.objects.filter(user=self.admin).update(active=False)
        self.assertContains(self.accept(Client(), revoked), 'Приглашение недействительно')
        self.assertFalse(get_user_model().objects.filter(email__in=['expired@example.com', 'revoked@example.com']).exists())

    def test_reissue_invalidates_previous_and_bad_password_keeps_pending(self):
        _, old = self.issue()
        _, new = self.issue()
        self.assertNotEqual(old, new)
        self.assertContains(Client().get(old), 'Приглашение недействительно')
        result = self.accept(Client(), new, new_password2='does-not-match')
        self.assertFalse(result.context.get('activated'))
        self.assertIsNone(ManagerInvitation.objects.get().used_at)
        self.assertFalse(get_user_model().objects.filter(email='new.manager@example.com').exists())

    def test_existing_account_never_reset_or_silently_granted(self):
        user = get_user_model().objects.create_user('existing', email='NEW.manager@example.com', password='existing-password')
        self.client.force_login(self.admin)
        response = self.client.post(reverse('procurement:invite_manager'), {'email': 'new.manager@example.com'})
        self.assertContains(response, 'уже существует')
        self.assertFalse(ManagerInvitation.objects.exists())
        self.assertFalse(Membership.objects.filter(user=user).exists())
        user.refresh_from_db()
        self.assertTrue(user.check_password('existing-password'))

    def test_account_created_after_issue_does_not_get_overwritten(self):
        _, path = self.issue()
        user = get_user_model().objects.create_user('other-login', email='new.manager@example.com', password='existing-password')
        self.assertContains(self.accept(Client(), path), 'Приглашение недействительно')
        self.assertFalse(Membership.objects.filter(user=user).exists())

    def test_only_admin_can_issue_and_csrf_required(self):
        url = reverse('procurement:invite_manager')
        self.assertEqual(self.client.post(url, {'email':'test@example.com'}).status_code, 302)
        member = get_user_model().objects.create_user('salesperson')
        Membership.objects.create(user=member, role='sales')
        self.client.force_login(member)
        self.assertEqual(self.client.post(url, {'email':'test@example.com'}).status_code, 403)
        self.assertFalse(ManagerInvitation.objects.exists())
        _, path = self.issue()
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse('procurement:activate_invitation'), {'new_password1':'something'}).status_code, 403)
        self.assertContains(Client().get(path.replace(path.rstrip('/').split('/')[-1], 'unknown')), 'Приглашение недействительно')

    def test_https_activation_requires_link_session_and_accepts_real_csrf(self):
        _, path = self.issue()
        clean_url = reverse('procurement:activate_invitation')
        self.assertContains(Client().get(clean_url), 'Приглашение недействительно')
        browser = Client(enforce_csrf_checks=True)
        landing = browser.get(path, secure=True)
        page = browser.get(landing.url, secure=True)
        self.assertContains(page, 'Активировать доступ')
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        csrf = browser.cookies['csrftoken'].value
        response = browser.post(clean_url, {'csrfmiddlewaretoken': csrf,
            'new_password1':'A-unique-secure-password-2026!',
            'new_password2':'A-unique-secure-password-2026!'}, secure=True,
            HTTP_ORIGIN='https://testserver')
        self.assertContains(response, 'Доступ активирован')
