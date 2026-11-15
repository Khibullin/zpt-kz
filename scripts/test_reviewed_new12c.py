import types
import unittest
import test_reviewed_new12a as h
from procurement import reviewed_new12c_fitment as mod


class Tests(unittest.TestCase):
    def setUp(self):
        with h.connection.cursor() as cursor:
            for model in reversed(h.MODELS):
                cursor.execute('DELETE FROM ' + model._meta.db_table)
        h.Supplier.objects.create(pk=3, name='Фаетон')
        h.Supplier.objects.create(pk=5, name='ZakazAuto — Костанай')
        self.apps = types.SimpleNamespace(get_model=lambda app, name: next(m for m in h.MODELS if m.__name__ == name))
        self.schema = types.SimpleNamespace(connection=h.connection)
        self.row = mod.payload()['offers'][0]
        r = self.row
        self.offer = h.Offer.objects.create(pk=r['id'], supplier_id=3, brand=r['brand'], article=r['article'],
            cost=r['cost'], stock=r['stock'], kaspi_price=r['kaspi_price'], characteristics={'keep': 'staff'})

    def test_exact_source_and_idempotence_preserve_trade(self):
        before = (self.offer.cost, self.offer.stock, self.offer.kaspi_price)
        mod.apply_review(self.apps, self.schema)
        self.assertEqual(h.VehicleFitment.objects.count(), 1)
        fit = h.VehicleFitment.objects.get()
        self.assertEqual(fit.configurations[0]['engine'], 'A25A-FXS')
        self.assertEqual(fit.source_url, self.row['source_url'])
        mod.apply_review(self.apps, self.schema)
        self.assertEqual(h.VehicleFitment.objects.count(), 1)
        self.offer.refresh_from_db()
        self.assertEqual((self.offer.cost, self.offer.stock, self.offer.kaspi_price), before)
        self.assertEqual(self.offer.characteristics['keep'], 'staff')

    def test_existing_fitment_not_replaced(self):
        h.VehicleFitment.objects.create(offer=self.offer, make='staff', model='existing', source_url='https://example.com')
        mod.apply_review(self.apps, self.schema)
        self.assertEqual(h.VehicleFitment.objects.get().make, 'staff')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.characteristics, {'keep': 'staff'})

    def test_changed_article_or_stock_not_touched(self):
        h.Offer.objects.filter(pk=self.offer.pk).update(article='1115-88', stock='0')
        mod.apply_review(self.apps, self.schema)
        self.assertFalse(h.VehicleFitment.objects.exists())

    def test_note_repair_preserves_staff_metadata_and_new_edit(self):
        r = mod.payload()['display_note_repairs'][0]
        meta = {'keep': 'staff', 'reviewed_applicability': {'source_url': r['source_url'],
            'checked_on': '2026-11-12', 'note': r['expected_note'], 'status': 'sourced'}}
        offer = h.Offer.objects.create(pk=r['offer_id'], supplier_id=5, brand=r['brand'], article=r['article'], characteristics=meta)
        mod.apply_review(self.apps, self.schema)
        offer.refresh_from_db()
        self.assertEqual(offer.characteristics['reviewed_applicability']['note'], r['note'])
        self.assertEqual(offer.characteristics['keep'], 'staff')
        offer.characteristics['reviewed_applicability']['note'] = 'New staff correction'
        offer.save()
        mod.apply_review(self.apps, self.schema)
        offer.refresh_from_db()
        self.assertEqual(offer.characteristics['reviewed_applicability']['note'], 'New staff correction')


if __name__ == '__main__':
    unittest.main()
