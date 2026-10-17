import copy
import importlib
import json
from pathlib import Path
from django.apps import apps
from django.db import transaction
from django.test import TestCase
from core.models import Seller, SellerExternalCard
from service_requests.models import ServiceSeller

batch = importlib.import_module('core.migrations.0095_import_almaty_parts_20261017')

class AlmatyPartsImportTests(TestCase):
    def setUp(self):
        self.payload = json.loads(batch.PAYLOAD.read_text())
        for plan in self.payload['plans']:
            if plan['seller']:
                Seller.objects.create(**plan['seller'], transport_type='car', receive_requests=True,
                    sells_new_parts=None, notes='Existing private note', is_dismantler=not plan['directory_parts'])
            if plan['service']:
                ServiceSeller.objects.create(**plan['service'], city='Алматы', receive_requests=True)

    def test_import_is_additive_idempotent_and_does_not_enable_dispatch(self):
        before = {s.pk:{k:getattr(s,k) for k in batch.PROTECTED} for s in Seller.objects.all()}
        result = batch.apply_batch(apps, self.payload)
        self.assertEqual(result, {'created':35,'updated':12,'services_updated':4})
        self.assertEqual(SellerExternalCard.objects.count(),47)
        for s in Seller.objects.filter(pk__in=before):
            self.assertEqual({k:getattr(s,k) for k in batch.PROTECTED}, before[s.pk])
        new = Seller.objects.exclude(pk__in=before)
        self.assertEqual(new.count(),35)
        self.assertFalse(new.filter(receive_requests=True).exists())
        self.assertFalse(new.filter(sells_new_parts=True).exists())
        self.assertFalse(new.filter(catalog_only=False).exists())
        second=batch.apply_batch(apps,self.payload)
        self.assertEqual(second,{'created':0,'updated':0,'services_updated':0})
        self.assertEqual(Seller.objects.count(),47)
        porsche=SellerExternalCard.objects.get(external_id='70000001023699747').seller_id
        audi=SellerExternalCard.objects.get(external_id='70000001034547358').seller_id
        self.assertNotEqual(porsche,audi)
        self.assertNotEqual(SellerExternalCard.objects.get(external_id='70000001117134333').seller_id,620)
        page=self.client.get('/parts-sellers/', {'q':'Lixiangshop'})
        self.assertEqual(page.context['page_obj'].paginator.count,1)
        seller=page.context['sellers'][0]
        profile=self.client.get(f'/parts-seller/{seller.pk}/')
        self.assertContains(profile,'Построить маршрут')
        self.assertNotContains(profile,'Режим работы')
        self.assertNotContains(profile,'tel:')

    def test_existing_data_wins_and_identity_conflicts_roll_back(self):
        seller=Seller.objects.get(pk=234)
        seller.address='Detailed verified address';seller.website='https://example.org/'
        seller.save(update_fields=['address','website'])
        payload=copy.deepcopy(self.payload)
        next(p for p in reversed(payload['plans']) if p['seller'])['seller']['name']='Wrong identity'
        with self.assertRaises(RuntimeError),transaction.atomic():
            batch.apply_batch(apps,payload)
        self.assertEqual(SellerExternalCard.objects.count(),0)
        batch.apply_batch(apps,self.payload)
        seller.refresh_from_db()
        self.assertEqual(seller.address,'Detailed verified address')
        self.assertEqual(seller.website,'https://example.org/')
