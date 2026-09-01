import json

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, SellerProfile
from core.models import Seller


REQUEST_PASSWORD = 'RequestPass123'
SHOP_PASSWORD = 'ShopPass12345'
NEW_PASSWORD = 'NewUnifiedPass1'


class UnifiedSellerAccountTests(TestCase):
    def _json_post(self, path, payload):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _request_login(self, whatsapp, password):
        return self._json_post(
            '/api/seller-login/',
            {'whatsapp': whatsapp, 'password': password},
        )

    def _shop_login(self, username, password, remember_me=False):
        data = {'username': username, 'password': password}
        if remember_me:
            data['remember_me'] = 'on'
        return self.client.post(reverse('seller_login'), data)

    def _create_product(self, profile, title='Уже существующий товар'):
        return Product.objects.create(
            title=title,
            seller_name=profile.name,
            whatsapp_number=profile.phone,
            seller_profile=profile,
            status='active',
            city=profile.city or 'Алматы',
        )

    def test_request_parts_registration_creates_unified_account(self):
        response = self._json_post('/api/create-seller/', {
            'name': 'Request Shop',
            'whatsapp': '77015550001',
            'password': REQUEST_PASSWORD,
            'password_confirm': REQUEST_PASSWORD,
            'transport_type': 'car',
            'city': 'Алматы',
        })
        self.assertEqual(response.status_code, 200, response.content)
        seller = Seller.objects.get(whatsapp='77015550001')
        user = User.objects.get(username='77015550001')
        profile = SellerProfile.objects.get(user=user)
        self.assertEqual(seller.user_id, user.id)
        self.assertEqual(profile.phone, '77015550001')
        self.assertEqual(seller.password_hash, '')
        self.assertFalse(seller.receive_requests)

        shop_login = self._shop_login('77015550001', REQUEST_PASSWORD)
        self.assertEqual(shop_login.status_code, 302)
        self.assertEqual(shop_login.url, reverse('seller_dashboard'))

    def test_shop_registration_creates_core_seller_and_request_login_works(self):
        response = self.client.post(reverse('seller_register'), {
            'name': 'Market Shop',
            'phone': '77015550002',
            'password': SHOP_PASSWORD,
            'city': 'Алматы',
            'address': 'ул. Регистрации, 1',
            'pickup_same_as_store': 'on',
            'pickup_available': 'on',
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='77015550002')
        profile = SellerProfile.objects.get(user=user)
        seller = Seller.objects.get(user=user)
        self.assertEqual(profile.phone, '77015550002')
        self.assertEqual(seller.whatsapp, '77015550002')
        self.assertEqual(seller.name, 'Market Shop')
        self.assertEqual(seller.seller_type, 'seller')
        self.assertEqual(seller.transport_type, 'car')
        self.assertFalse(seller.receive_requests)
        self.assertTrue(seller.is_active)
        self.assertFalse(seller.is_paused)
        self.assertEqual(seller.password_hash, '')

        login_response = self._request_login('77015550002', SHOP_PASSWORD)
        self.assertEqual(login_response.status_code, 200, login_response.content)
        self.assertEqual(login_response.json()['status'], 'ok')

    def test_request_cabinet_login_opens_shop_dashboard(self):
        user, seller, profile = self._register_request_account('77015550003')
        response = self._request_login('77015550003', REQUEST_PASSWORD)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(self.client.session.get('_auth_user_id')), str(user.id))
        self.assertEqual(self.client.session.get('seller_id'), seller.id)

        dashboard = self.client.get(reverse('seller_dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        profile_api = self.client.get('/api/seller-profile/')
        self.assertEqual(profile_api.status_code, 200)

    def test_shop_login_opens_request_cabinet(self):
        user, seller, profile = self._register_shop_account('77015550004')
        response = self._shop_login('77015550004', SHOP_PASSWORD)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('seller_id'), seller.id)
        self.assertEqual(str(self.client.session.get('_auth_user_id')), str(user.id))

        cabinet = self.client.get('/api/seller-profile/')
        self.assertEqual(cabinet.status_code, 200)
        self.assertEqual(cabinet.json()['id'], seller.id)

    def test_request_password_change_updates_user_for_both_logins(self):
        user, seller, _profile = self._register_request_account('77015550005')
        self._request_login('77015550005', REQUEST_PASSWORD)
        change = self._json_post('/api/change-seller-password/', {
            'old_password': REQUEST_PASSWORD,
            'new_password': NEW_PASSWORD,
            'new_password_confirm': NEW_PASSWORD,
        })
        self.assertEqual(change.status_code, 200, change.content)
        user.refresh_from_db()
        seller.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertFalse(user.check_password(REQUEST_PASSWORD))
        self.assertEqual(seller.password_hash, '')

        self.client.post('/api/seller-logout/')
        self.assertEqual(self._request_login('77015550005', REQUEST_PASSWORD).status_code, 400)
        shop_old = self._shop_login('77015550005', REQUEST_PASSWORD)
        self.assertEqual(shop_old.status_code, 200)
        self.assertContains(shop_old, 'Неверный логин или пароль')
        self.assertEqual(self._request_login('77015550005', NEW_PASSWORD).status_code, 200)
        self.client.post('/api/seller-logout/')
        shop = self._shop_login('77015550005', NEW_PASSWORD)
        self.assertEqual(shop.status_code, 302)
        self.assertEqual(shop.url, reverse('seller_dashboard'))

    def test_shop_password_change_works_in_request_login(self):
        user, seller, _profile = self._register_shop_account('77015550006')
        self._shop_login('77015550006', SHOP_PASSWORD)
        response = self.client.post(reverse('seller_change_password'), {
            'current_password': SHOP_PASSWORD,
            'new_password': NEW_PASSWORD,
            'confirm_password': NEW_PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))

        self.client.get(reverse('seller_logout'))
        old_login = self._request_login('77015550006', SHOP_PASSWORD)
        self.assertEqual(old_login.status_code, 400)
        new_login = self._request_login('77015550006', NEW_PASSWORD)
        self.assertEqual(new_login.status_code, 200, new_login.content)

    def test_admin_reset_changes_user_password_not_legacy_hash(self):
        seller = Seller.objects.create(
            name='Кабинет продавца',
            whatsapp='77015550007',
            transport_type='car',
            city='Алматы',
            password_hash=make_password('OldSellerPass1'),
            must_change_password=True,
        )
        user = User.objects.create_user(username='77015550007', password='DjangoUserPass1')
        admin = User.objects.create_superuser(
            username='admin-unified',
            email='admin-unified@example.com',
            password='AdminPass123',
        )
        self.client.force_login(admin)
        response = self.client.post(
            reverse('admin_seller_reset_password', args=[seller.pk]),
            {
                'new_password': NEW_PASSWORD,
                'new_password_confirm': NEW_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)
        seller.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(seller.user_id, user.id)
        self.assertEqual(seller.password_hash, '')
        self.assertFalse(seller.must_change_password)
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertFalse(user.check_password('DjangoUserPass1'))

        self.client.logout()
        self.assertEqual(self._request_login('77015550007', 'OldSellerPass1').status_code, 400)
        self.assertEqual(self._request_login('77015550007', NEW_PASSWORD).status_code, 200)

    def test_dual_account_accepts_only_user_password(self):
        user = User.objects.create_user(username='77015550008', password=REQUEST_PASSWORD)
        profile = SellerProfile.objects.create(
            user=user,
            name='Dual Shop',
            phone='77015550008',
            city='Алматы',
        )
        seller = Seller.objects.create(
            user=user,
            name='Dual Shop',
            whatsapp='77015550008',
            transport_type='car',
            password_hash=make_password('LegacyHashPass1'),
            must_change_password=False,
            receive_requests=True,
            seller_type='seller',
        )
        product = self._create_product(profile)
        self.assertEqual(self._request_login('77015550008', 'LegacyHashPass1').status_code, 400)
        ok = self._request_login('77015550008', REQUEST_PASSWORD)
        self.assertEqual(ok.status_code, 200, ok.content)
        seller.refresh_from_db()
        self.assertEqual(seller.password_hash, '')
        self.assertTrue(seller.receive_requests)
        product.refresh_from_db()
        self.assertEqual(product.seller_profile_id, profile.id)

    def test_legacy_request_only_seller_migrates_on_first_login(self):
        seller = Seller.objects.create(
            name='Legacy Request',
            whatsapp='77015550009',
            transport_type='car',
            city='Алматы',
            password_hash=make_password('LegacyPass123'),
            must_change_password=True,
            receive_requests=True,
            seller_type='seller',
        )
        self.assertFalse(User.objects.filter(username='77015550009').exists())
        response = self._request_login('77015550009', 'LegacyPass123')
        self.assertEqual(response.status_code, 200, response.content)
        seller.refresh_from_db()
        user = User.objects.get(username='77015550009')
        profile = SellerProfile.objects.get(user=user)
        self.assertEqual(seller.user_id, user.id)
        self.assertEqual(seller.password_hash, '')
        self.assertFalse(seller.must_change_password)
        self.assertTrue(seller.receive_requests)
        self.assertEqual(profile.phone, '77015550009')
        self.assertTrue(user.check_password('LegacyPass123'))

    def test_shop_only_login_creates_request_seller_without_live_dispatch(self):
        user = User.objects.create_user(username='77015550010', password=SHOP_PASSWORD)
        SellerProfile.objects.create(
            user=user,
            name='Shop Only',
            phone='77015550010',
            city='Алматы',
        )
        self.assertFalse(Seller.objects.filter(user=user).exists())
        response = self._shop_login('77015550010', SHOP_PASSWORD)
        self.assertEqual(response.status_code, 302)
        seller = Seller.objects.get(user=user)
        self.assertFalse(seller.receive_requests)
        self.assertEqual(seller.whatsapp, '77015550010')
        self.assertEqual(self.client.session.get('seller_id'), seller.id)

    def test_logout_from_either_portal_clears_both_auth_contexts(self):
        self._register_request_account('77015550011')
        self._request_login('77015550011', REQUEST_PASSWORD)
        self.assertTrue(self.client.session.get('_auth_user_id'))
        self.assertIsNotNone(self.client.session.get('seller_id'))

        self.client.post('/api/seller-logout/')
        self.assertIsNone(self.client.session.get('seller_id'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get('/api/seller-profile/').status_code, 401)
        dashboard = self.client.get(reverse('seller_dashboard'))
        self.assertEqual(dashboard.status_code, 302)

        self._shop_login('77015550011', REQUEST_PASSWORD)
        self.assertTrue(self.client.session.get('_auth_user_id'))
        self.client.get(reverse('seller_logout'))
        self.assertIsNone(self.client.session.get('seller_id'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get('/api/seller-profile/').status_code, 401)

    def test_phone_change_syncs_user_seller_and_profile(self):
        user, seller, profile = self._register_request_account('77015550012')
        self._request_login('77015550012', REQUEST_PASSWORD)
        response = self._json_post('/api/update-seller-profile/', {
            'whatsapp': '+7 (701) 555-00-99',
        })
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        seller.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(user.username, '77015550099')
        self.assertEqual(seller.whatsapp, '77015550099')
        self.assertEqual(profile.phone, '77015550099')

    def test_conflict_phone_is_rejected(self):
        self._register_request_account('77015550013')
        user, seller, profile = self._register_request_account('77015550014')
        self._request_login('77015550014', REQUEST_PASSWORD)
        response = self._json_post('/api/update-seller-profile/', {
            'whatsapp': '77015550013',
        })
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        seller.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(user.username, '77015550014')
        self.assertEqual(seller.whatsapp, '77015550014')
        self.assertEqual(profile.phone, '77015550014')

    def test_existing_products_and_seller_profile_ownership_untouched(self):
        user = User.objects.create_user(username='77015550015', password=SHOP_PASSWORD)
        profile = SellerProfile.objects.create(
            user=user,
            name='Owned Shop',
            phone='77015550015',
            city='Алматы',
        )
        product = self._create_product(profile, title='Капот Camry')
        other_user = User.objects.create_user(username='77015550016', password=SHOP_PASSWORD)
        other_profile = SellerProfile.objects.create(
            user=other_user,
            name='Other Shop',
            phone='77015550016',
            city='Алматы',
        )
        other_product = self._create_product(other_profile, title='Чужой товар')

        self._shop_login('77015550015', SHOP_PASSWORD)
        product.refresh_from_db()
        other_product.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(product.seller_profile_id, profile.id)
        self.assertEqual(other_product.seller_profile_id, other_profile.id)
        self.assertEqual(profile.user_id, user.id)
        self.assertEqual(Product.objects.get(pk=product.pk).title, 'Капот Camry')

    def test_html_request_registration_also_creates_unified_account(self):
        response = self.client.post(reverse('register_seller'), {
            'company_name': 'Landing Shop',
            'city': 'Алматы',
            'whatsapp_phone': '77015550017',
            'password': REQUEST_PASSWORD,
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='77015550017')
        seller = Seller.objects.get(user=user)
        profile = SellerProfile.objects.get(user=user)
        self.assertEqual(seller.whatsapp, '77015550017')
        self.assertEqual(profile.phone, '77015550017')
        self.assertEqual(seller.password_hash, '')

    def _register_request_account(self, phone):
        created = self._json_post('/api/create-seller/', {
            'name': f'Shop {phone}',
            'whatsapp': phone,
            'password': REQUEST_PASSWORD,
            'password_confirm': REQUEST_PASSWORD,
            'transport_type': 'car',
            'city': 'Алматы',
        })
        self.assertEqual(created.status_code, 200, created.content)
        user = User.objects.get(username=phone)
        return user, Seller.objects.get(user=user), SellerProfile.objects.get(user=user)

    def _register_shop_account(self, phone):
        created = self.client.post(reverse('seller_register'), {
            'name': f'Market {phone}',
            'phone': phone,
            'password': SHOP_PASSWORD,
            'city': 'Алматы',
            'pickup_same_as_store': 'on',
            'pickup_available': 'on',
        })
        self.assertEqual(created.status_code, 302, created.content)
        user = User.objects.get(username=phone)
        return user, Seller.objects.get(user=user), SellerProfile.objects.get(user=user)
