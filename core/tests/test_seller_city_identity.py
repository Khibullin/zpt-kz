from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.models import SellerProfile
from core.models import Seller
from core.services.seller_identity import (
    SellerIdentityError,
    create_unified_seller_account,
)


class SellerCityIdentityTests(TestCase):
    def test_registration_canonicalizes_city_for_both_seller_records(self):
        user, seller, profile = create_unified_seller_account(
            name='City Alias Seller',
            whatsapp='77015558801',
            password='City-test-123!',
            city='Almaty',
            transport_type='car',
        )

        self.assertEqual(user.username, '77015558801')
        self.assertEqual(seller.city, 'Алматы')
        self.assertEqual(profile.city, 'Алматы')
        self.assertEqual(Seller.objects.count(), 1)
        self.assertEqual(SellerProfile.objects.count(), 1)

    def test_registration_rejects_unknown_city_without_partial_account(self):
        with self.assertRaises(SellerIdentityError):
            create_unified_seller_account(
                name='Unknown City Seller',
                whatsapp='77015558802',
                password='City-test-123!',
                city='Unknown City',
                transport_type='car',
            )

        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(Seller.objects.count(), 0)
        self.assertEqual(SellerProfile.objects.count(), 0)
