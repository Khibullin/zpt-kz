from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings

from catalog.models import Product, SellerProfile
from core.models import Seller
from core.services.registered_seller_public_audit import (
    STATE_TABLE,
    _apply_curated_profile_facts,
    _phone_values_in_text,
    process_registered_seller_public_audit_batch,
)


class RegisteredSellerPublicAuditTests(TestCase):
    def _seller(self, *, seller_id=700, name='Audit Shop', phone='77011234567', city='Алматы'):
        user = get_user_model().objects.create_user(
            username=f'audit-{seller_id}',
            password='test-password',
        )
        seller = Seller.objects.create(
            id=seller_id,
            name=name,
            whatsapp=phone,
            city=city,
            transport_type='car',
            user=user,
        )
        profile = SellerProfile.objects.create(
            user=user,
            name=name,
            phone=phone,
            city=city,
        )
        return seller, profile

    def _state(self, seller_id):
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {STATE_TABLE}
                    (seller_id, status, attempts, result, error, updated_at)
                VALUES (%s, 'pending', 0, '{{}}', '', CURRENT_TIMESTAMP)
                ON CONFLICT (seller_id) DO UPDATE
                SET status='pending', attempts=0, result='{{}}',
                    error='', started_at=NULL, finished_at=NULL, updated_at=CURRENT_TIMESTAMP
                """,
                [seller_id],
            )

    def test_phone_parser_requires_real_kz_phone(self):
        values = _phone_values_in_text('WhatsApp +7 (701) 123-45-67, код 1234')
        self.assertEqual(values, {'77011234567'})

    def test_curated_fact_only_fills_exact_identity(self):
        seller, profile = self._seller(
            seller_id=527,
            name='Tiptronic',
            phone='77023226888',
            city='Шымкент',
        )
        changed = _apply_curated_profile_facts()
        self.assertEqual(changed, 1)
        profile.refresh_from_db()
        self.assertEqual(profile.website, 'http://www.t-tronic.kz/')
        self.assertEqual(
            profile.instagram,
            'https://www.instagram.com/tiptronic_autoparts/',
        )
        seller.refresh_from_db()
        self.assertEqual(seller.whatsapp, '77023226888')

    @override_settings(BRAVE_SEARCH_API_KEY='')
    def test_batch_is_bounded_and_does_not_create_business_rows(self):
        seller, profile = self._seller(seller_id=701)
        self._state(seller.id)
        before = (
            Seller.objects.count(),
            SellerProfile.objects.count(),
            Product.objects.count(),
            seller.whatsapp,
            seller.receive_requests,
        )

        result = process_registered_seller_public_audit_batch(batch_size=1)

        seller.refresh_from_db()
        profile.refresh_from_db()
        after = (
            Seller.objects.count(),
            SellerProfile.objects.count(),
            Product.objects.count(),
            seller.whatsapp,
            seller.receive_requests,
        )
        self.assertEqual(before, after)
        self.assertEqual(result.claimed, 1)
        self.assertEqual(result.completed, 1)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT status FROM {STATE_TABLE} WHERE seller_id=%s",
                [seller.id],
            )
            self.assertEqual(cursor.fetchone()[0], 'done')

    def test_one_failure_is_recorded_and_batch_continues(self):
        first, _ = self._seller(seller_id=702, name='First')
        second, _ = self._seller(seller_id=703, name='Second')
        self._state(first.id)
        self._state(second.id)

        def fake_audit(seller_id):
            if seller_id == first.id:
                raise RuntimeError('controlled')
            return {'outcome': 'checked', 'seller': 'Second'}

        with patch(
            'core.services.registered_seller_public_audit._audit_one',
            side_effect=fake_audit,
        ):
            result = process_registered_seller_public_audit_batch(batch_size=2)

        self.assertEqual(result.claimed, 2)
        self.assertEqual(result.completed, 2)
        self.assertEqual(result.errors, 1)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT seller_id,status,error FROM {STATE_TABLE} "
                "WHERE seller_id IN (%s,%s) ORDER BY seller_id",
                [first.id, second.id],
            )
            rows = cursor.fetchall()
        self.assertEqual(rows[0][1], 'done')
        self.assertIn('controlled', rows[0][2])
        self.assertEqual(rows[1][1], 'done')
