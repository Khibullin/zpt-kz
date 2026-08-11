from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.forms import SellerProfileForm, SellerRegisterForm
from catalog.models import SellerProfile


class SellerPickupFormTests(TestCase):
    def test_register_form_same_as_store_copies_address(self):
        form = SellerRegisterForm(data={
            'name': 'Market A',
            'phone': '77771112233',
            'password': 'secret12345',
            'city': 'Алматы',
            'address': 'Магазин, ул. Первая, 1',
            'pickup_same_as_store': True,
            'pickup_available': True,
            'pickup_address': 'должен игнорироваться',
            'work_hours': '',
            'delivery_info': '',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['pickup_address'], 'Магазин, ул. Первая, 1')
        self.assertTrue(form.cleaned_data['pickup_same_as_store'])

    def test_register_form_separate_pickup_address(self):
        form = SellerRegisterForm(data={
            'name': 'Market B',
            'phone': '77771112234',
            'password': 'secret12345',
            'city': 'Алматы',
            'address': 'Офис, ул. Вторая, 2',
            'pickup_same_as_store': False,
            'pickup_available': True,
            'pickup_address': 'Склад, ул. Третья, 3',
            'work_hours': '',
            'delivery_info': '',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['pickup_address'], 'Склад, ул. Третья, 3')

    def test_register_form_pickup_unavailable_allows_empty_pickup(self):
        form = SellerRegisterForm(data={
            'name': 'Market C',
            'phone': '77771112235',
            'password': 'secret12345',
            'city': 'Алматы',
            'address': '',
            'pickup_same_as_store': False,
            'pickup_available': False,
            'pickup_address': '',
            'work_hours': '',
            'delivery_info': '',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data['pickup_available'])

    def test_register_form_requires_pickup_when_available_and_separate(self):
        form = SellerRegisterForm(data={
            'name': 'Market D',
            'phone': '77771112236',
            'password': 'secret12345',
            'city': 'Алматы',
            'address': 'Офис',
            'pickup_same_as_store': False,
            'pickup_available': True,
            'pickup_address': '',
            'work_hours': '',
            'delivery_info': '',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('pickup_address', form.errors)

    def test_profile_form_syncs_address_when_same_as_store(self):
        user = User.objects.create_user(username='77770001122', password='secret12345')
        seller = SellerProfile.objects.create(
            user=user,
            name='Edit Market',
            phone='77770001122',
            address='Старый адрес',
            pickup_address='Старый адрес',
            pickup_same_as_store=True,
            pickup_available=True,
        )
        form = SellerProfileForm(
            data={
                'name': 'Edit Market',
                'phone': '77770001122',
                'city': 'Алматы',
                'address': 'Новый магазин, 10',
                'pickup_same_as_store': True,
                'pickup_available': True,
                'pickup_address': 'устаревший',
                'work_hours': '',
                'delivery_info': '',
                'instagram': '',
                'website': '',
                'description': '',
            },
            instance=seller,
        )
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.address, 'Новый магазин, 10')
        self.assertEqual(updated.pickup_address, 'Новый магазин, 10')

    def test_register_view_persists_pickup_fields(self):
        response = self.client.post(reverse('seller_register'), data={
            'name': 'View Market',
            'phone': '77775556677',
            'password': 'secret12345',
            'city': 'Алматы',
            'address': 'ул. Регистрации, 1',
            'pickup_same_as_store': 'on',
            'pickup_available': 'on',
            'pickup_address': '',
            'work_hours': '',
            'delivery_info': '',
        })
        self.assertEqual(response.status_code, 302)
        seller = SellerProfile.objects.get(phone='77775556677')
        self.assertEqual(seller.address, 'ул. Регистрации, 1')
        self.assertEqual(seller.pickup_address, 'ул. Регистрации, 1')
        self.assertTrue(seller.pickup_available)
        self.assertTrue(seller.pickup_same_as_store)


class SellerPickupMigrationDataTests(TestCase):
    def test_existing_seller_with_address_gets_pickup_defaults(self):
        """Simulate post-migration state for sellers that already had address."""
        user = User.objects.create_user(username='77770009988', password='secret12345')
        seller = SellerProfile(
            user=user,
            name='Legacy Seller',
            phone='77770009988',
            address='г. Алматы, старый склад 1',
        )
        # Bypass save sync by writing fields as migration would.
        seller.pickup_address = ''
        seller.pickup_same_as_store = True
        seller.pickup_available = True
        SellerProfile.objects.bulk_create([seller])
        seller = SellerProfile.objects.get(pk=seller.pk)

        # Apply the same data-migration rules.
        address = (seller.address or '').strip()
        if address:
            seller.pickup_address = address
            seller.pickup_same_as_store = True
            seller.pickup_available = True
        else:
            seller.pickup_address = ''
            seller.pickup_same_as_store = True
            seller.pickup_available = False
        seller.save()

        seller.refresh_from_db()
        self.assertEqual(seller.pickup_address, 'г. Алматы, старый склад 1')
        self.assertTrue(seller.pickup_available)
        self.assertTrue(seller.pickup_same_as_store)

    def test_existing_seller_without_address_disables_pickup(self):
        user = User.objects.create_user(username='77770009989', password='secret12345')
        seller = SellerProfile.objects.create(
            user=user,
            name='Empty Address Seller',
            phone='77770009989',
            address='',
            pickup_address='should clear',
            pickup_available=True,
            pickup_same_as_store=True,
        )
        address = (seller.address or '').strip()
        if address:
            seller.pickup_address = address
            seller.pickup_same_as_store = True
            seller.pickup_available = True
        else:
            seller.pickup_address = ''
            seller.pickup_same_as_store = True
            seller.pickup_available = False
        seller.save()
        seller.refresh_from_db()
        self.assertEqual(seller.pickup_address, '')
        self.assertFalse(seller.pickup_available)
