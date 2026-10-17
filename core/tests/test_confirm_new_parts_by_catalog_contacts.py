"""Existing active NEW products can confirm only exact seller+phone matches."""
from importlib import import_module

from django.apps import apps
from django.test import TestCase

from catalog.models import Product
from core.models import Seller


def make_seller(*, pk, name, phone, confirmed=None):
    return Seller.objects.create(
        pk=pk, name=name, whatsapp=phone, transport_type='car',
        city='Алматы', sells_new_parts=confirmed,
    )


def make_product(*, name, phone, condition='new', status='active'):
    return Product.objects.create(
        title='Test spare part', article='TEST-' + str(Product.objects.count() + 1),
        seller_name=name, whatsapp_number=phone, condition=condition, status=status,
    )


class ExistingCatalogProofTests(TestCase):
    def test_exact_name_and_phone_confirm_new_without_changing_contacts(self):
        seller = make_seller(pk=302, name='"GRM4x4"', phone='87015551112')
        make_product(name='grm4x4', phone='+77015551112')
        migration = import_module('core.migrations.0090_confirm_new_parts_by_exact_catalog_contacts')
        migration.verify_existing_new_parts_by_catalog(apps, None)
        seller.refresh_from_db()
        self.assertTrue(seller.sells_new_parts)
        self.assertEqual(seller.whatsapp, '87015551112')

    def test_name_only_is_insufficient(self):
        seller = make_seller(pk=414, name='KOREA-PARTS', phone='77015551111')
        make_product(name='KOREA-PARTS', phone='77015551112')
        migration = import_module('core.migrations.0090_confirm_new_parts_by_exact_catalog_contacts')
        migration.verify_existing_new_parts_by_catalog(apps, None)
        seller.refresh_from_db()
        self.assertIsNone(seller.sells_new_parts)

    def test_shared_phone_and_used_products_do_not_confirm_new(self):
        seller = make_seller(pk=415, name='AUTOLEADER', phone='77015552222')
        shared = make_seller(pk=999, name='Another company', phone='77015552222')
        make_product(name='AUTOLEADER', phone='77015552222')
        migration = import_module('core.migrations.0090_confirm_new_parts_by_exact_catalog_contacts')
        migration.verify_existing_new_parts_by_catalog(apps, None)
        seller.refresh_from_db()
        shared.refresh_from_db()
        self.assertIsNone(seller.sells_new_parts)
        self.assertIsNone(shared.sells_new_parts)

        shared.whatsapp = '77016663333'
        shared.save(update_fields=['whatsapp'])
        Product.objects.all().update(condition='used')
        migration.verify_existing_new_parts_by_catalog(apps, None)
        seller.refresh_from_db()
        self.assertIsNone(seller.sells_new_parts)

    def test_explicit_no_is_not_overridden(self):
        seller = make_seller(
            pk=415, name='AUTOLEADER', phone='77015553333', confirmed=False,
        )
        make_product(name='AUTOLEADER', phone='77015553333')
        migration = import_module('core.migrations.0090_confirm_new_parts_by_exact_catalog_contacts')
        migration.verify_existing_new_parts_by_catalog(apps, None)
        seller.refresh_from_db()
        self.assertFalse(seller.sells_new_parts)
