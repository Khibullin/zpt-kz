"""Regression: evidence-based seller specialization corrections are narrow and idempotent."""
from importlib import import_module

from django.apps import apps
from django.test import TestCase

from core.models import Seller


def seller(*, pk, name, number, dismantler=False, new=None):
    return Seller.objects.create(
        pk=pk,
        name=name,
        whatsapp=number,
        transport_type='car',
        city='Алматы',
        is_dismantler=dismantler,
        sells_new_parts=new,
        receive_requests=True,
    )


class SellerSpecializationCorrectionsTests(TestCase):
    def test_only_exact_known_sellers_change(self):
        explicit = seller(pk=133, name='Razbor777.kz', number='77015550101')
        renamed = seller(pk=173, name='Different company', number='77015550102')
        dealer = seller(
            pk=446,
            name='BMW Алматы (@bavaria_almaty) • Instagram photos and videos',
            number='77015550103',
            new=True,
        )
        dismantler = seller(
            pk=670, name='Авторазбор BUU — Toyota Camry',
            number='77015550104', dismantler=True, new=True,
        )
        unrelated = seller(
            pk=998, name='Razbor777.kz', number='77015550105',
        )
        mod = import_module('core.migrations.0089_correct_existing_seller_specializations')
        mod.correct_existing_seller_specializations(apps, None)

        for obj in (explicit, renamed, dealer, dismantler, unrelated):
            obj.refresh_from_db()
        self.assertTrue(explicit.is_dismantler)
        self.assertIsNone(explicit.sells_new_parts)
        self.assertFalse(renamed.is_dismantler)
        self.assertFalse(unrelated.is_dismantler)
        self.assertIsNone(dealer.sells_new_parts)
        self.assertFalse(dealer.is_dismantler)
        self.assertTrue(dismantler.is_dismantler)
        self.assertIsNone(dismantler.sells_new_parts)
        self.assertTrue(all(s.receive_requests for s in (
            explicit, renamed, dealer, dismantler, unrelated
        )))

        mod.correct_existing_seller_specializations(apps, None)
        explicit.refresh_from_db()
        dealer.refresh_from_db()
        self.assertTrue(explicit.is_dismantler)
        self.assertIsNone(dealer.sells_new_parts)

    def test_positive_new_parts_evidence_is_preserved(self):
        known = seller(
            pk=129, name='MPARTS.KZ', number='77015550110',
            new=True,
        )
        user_added = seller(
            pk=997, name='New parts and used parts', number='77015550111',
            new=True, dismantler=True,
        )
        mod = import_module('core.migrations.0089_correct_existing_seller_specializations')
        mod.correct_existing_seller_specializations(apps, None)
        known.refresh_from_db()
        user_added.refresh_from_db()
        self.assertTrue(known.sells_new_parts)
        self.assertTrue(user_added.sells_new_parts)
        self.assertTrue(user_added.is_dismantler)
