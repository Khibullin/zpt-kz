import importlib
import json

from django.apps import apps
from django.db import transaction
from django.test import TestCase
from core.models import Seller, SellerExternalCard, SellerExtraPhone

batch = importlib.import_module('core.migrations.0100_import_almaty_dismantlers_206_20261018')


class Almaty206ImportTests(TestCase):
    def setUp(self):
        self.payload = json.loads(batch.PAYLOAD.read_text())
        for plan in self.payload['plans']:
            expected = plan['seller']
            if expected and not Seller.objects.filter(pk=expected['id']).exists():
                Seller.objects.create(**expected, transport_type='car',
                                      is_dismantler=not plan['confirm_dismantler'],
                                      receive_requests=False, notes='Private note')

    def test_repeatable_city_and_contact_handling(self):
        before = {s.pk: (s.receive_requests, s.notes, s.catalog_only, s.city, s.whatsapp)
                  for s in Seller.objects.all()}
        self.assertEqual(batch.apply_batch(apps, self.payload)['created'], 106)
        self.assertEqual(SellerExternalCard.objects.count(), 185)
        self.assertEqual(Seller.objects.count(), 183)
        for seller in Seller.objects.filter(pk__in=before):
            self.assertEqual((seller.receive_requests, seller.notes, seller.catalog_only,
                              seller.city, seller.whatsapp), before[seller.pk])
        self.assertEqual(Seller.objects.filter(pk__in=(425, 426, 433), is_dismantler=True).count(), 3)
        new = Seller.objects.exclude(pk__in=before)
        self.assertEqual(new.filter(city='Алматы').count(), 104)
        self.assertEqual(new.filter(city='Алатау').count(), 2)
        self.assertEqual(new.filter(receive_requests=True).count(), 97)
        self.assertEqual(new.filter(whatsapp='', receive_requests=False).count(), 9)
        self.assertFalse(new.filter(sells_new_parts=True).exists())
        self.assertEqual(batch.apply_batch(apps, self.payload),
                         {'created': 0, 'updated': 0, 'cards_added': 0})

    def test_concurrent_contact_conflict_rolls_back(self):
        target = next(p for p in reversed(self.payload['plans']) if not p['seller'])
        intruder = Seller.objects.create(name='Other organisation', whatsapp='70000000000',
                                         transport_type='car')
        SellerExtraPhone.objects.create(seller=intruder, phone=target['org']['phones'][0])
        initial = Seller.objects.count()
        with self.assertRaises(RuntimeError), transaction.atomic():
            batch.apply_batch(apps, self.payload)
        self.assertEqual(Seller.objects.count(), initial)
        self.assertEqual(SellerExternalCard.objects.count(), 0)
