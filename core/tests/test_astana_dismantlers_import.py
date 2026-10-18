import copy
import importlib
import json

from django.apps import apps
from django.db import transaction
from django.test import TestCase
from core.models import Seller, SellerExternalCard, SellerExtraPhone

batch = importlib.import_module('core.migrations.0099_import_astana_dismantlers_20261018')


class AstanaImportTests(TestCase):
    def setUp(self):
        self.payload = json.loads(batch.PAYLOAD.read_text())
        for plan in self.payload['plans']:
            expected = plan['seller']
            if expected and not Seller.objects.filter(pk=expected['id']).exists():
                Seller.objects.create(**expected, transport_type='car', is_dismantler=True,
                                      receive_requests=False, notes='Keep private note')

    def test_repeatable_preserves_existing_flags_and_handles_branches(self):
        before = {s.pk: (s.receive_requests, s.notes, s.catalog_only) for s in Seller.objects.all()}
        first = batch.apply_batch(apps, self.payload)
        self.assertEqual(first['created'], 25)
        self.assertEqual(first['cards_added'], 74)
        self.assertEqual(Seller.objects.count(), 73)
        for seller in Seller.objects.filter(pk__in=before):
            self.assertEqual((seller.receive_requests, seller.notes, seller.catalog_only), before[seller.pk])
        self.assertEqual(SellerExternalCard.objects.filter(seller_id=761).count(), 2)
        self.assertEqual(Seller.objects.get(pk=638).name, 'Avtorazbor.kz')
        new = Seller.objects.exclude(pk__in=before)
        self.assertEqual(new.filter(receive_requests=True).count(), 23)
        self.assertEqual(new.filter(city='Косшы').count(), 1)
        self.assertEqual(new.get(transport_type='truck').name, 'СТО грузовых авто')
        self.assertFalse(new.filter(sells_new_parts=True).exists())
        self.assertEqual(batch.apply_batch(apps, self.payload), {'created': 0, 'updated': 0, 'cards_added': 0})

    def test_conflicts_roll_back_and_nonempty_values_survive(self):
        seller = Seller.objects.get(pk=761)
        seller.address = 'Verified address'
        seller.save(update_fields=['address'])
        intruder = Seller.objects.create(name='Conflict', whatsapp='70000000000', transport_type='car')
        number = self.payload['plans'][-2]['org']['phones'][0]
        SellerExtraPhone.objects.create(seller=intruder, phone=number)
        initial = Seller.objects.count()
        with self.assertRaises(RuntimeError), transaction.atomic():
            batch.apply_batch(apps, self.payload)
        self.assertEqual(Seller.objects.count(), initial)
        self.assertEqual(SellerExternalCard.objects.count(), 0)
        intruder.delete()
        batch.apply_batch(apps, self.payload)
        seller.refresh_from_db()
        self.assertEqual(seller.address, 'Verified address')
