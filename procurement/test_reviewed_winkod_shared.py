from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from procurement.models import Supplier, Offer, VehicleFitment

migration = import_module('procurement.migrations.0033_reviewed_winkod_shared')

class ReviewedWinkodTests(TestCase):
    def test_bounded_copy_keeps_trade_data_and_is_idempotent(self):
        get_user_model().objects.create_superuser(pk=1, username='admin', password='test')
        Supplier.objects.create(pk=3, name='Фаетон')
        Supplier.objects.create(pk=5, name='ZakazAuto')
        for target_id, (article, source_id) in migration.TARGETS.items():
            source = Offer.objects.create(pk=source_id, supplier_id=5, brand='WINKOD', article=article, cost=1)
            VehicleFitment.objects.create(offer=source, make='KIA', model='Rio', verified=True,
                source_url='https://shop.globalparts.kz/product/winkod/' + article.lower(),
                configurations=[{'years':'2005','notes':'front right'}])
            Offer.objects.create(pk=target_id, supplier_id=3, brand='WINKOD', article=article, cost=999,
                stock='4', characteristics={'supplier_pack_size':1})
        wrong = Offer.objects.get(pk=55289)
        wrong.article += 'X'; wrong.save()
        schema = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        migration.apply_review(apps, schema)
        migration.apply_review(apps, schema)
        self.assertEqual(VehicleFitment.objects.filter(offer__supplier_id=3).count(), 3)
        self.assertFalse(wrong.fitments.exists())
        for target in Offer.objects.filter(supplier_id=3).exclude(pk=55289):
            self.assertEqual(target.cost, 999)
            self.assertEqual(target.stock,'4')
            self.assertEqual(target.characteristics['supplier_pack_size'],1)
            self.assertEqual(target.fitments.get().configurations,[{'years':'2005','notes':'front right'}])
