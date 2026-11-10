import json
from datetime import date
from io import StringIO
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Supplier, Offer, VehicleFitment
from .shared_fitments import fill_from_shared_base
from .catalogue_import import import_catalogue_fitments


class SharedFitmentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='fitment_admin', is_superuser=True, is_staff=True)
        self.source = Supplier.objects.create(name='Omega')
        self.target = Supplier.objects.create(name='ZakazAuto')
        self.offer = Offer.objects.create(supplier=self.source, brand='MOTORAD', article='2028-77K', name='Thermostat', cost=1000)
        self.other = Offer.objects.create(supplier=self.target, brand='MOTORAD', article='2028-77K', name='Thermostat', cost=2000, stock='3', kaspi_price=5000, characteristics={'kaspi_identity': {'confirmed': True}})
        self.fit = VehicleFitment.objects.create(offer=self.offer, make='NISSAN', model='PRIMERA', source_url='https://eu.motorad.com/part/2028-77k/', verified=True, configurations=[{'years':'1996', 'engine':'SR20DE', 'notes':'Date To: 02/03'}])

    def test_exact_shared_copy_is_idempotent_and_keeps_restrictions_and_trade_data(self):
        result = fill_from_shared_base(self.target, self.user)
        self.assertEqual(result['offers'], 1)
        copied = self.other.fitments.get()
        self.assertEqual(copied.configurations, self.fit.configurations)
        self.assertEqual(copied.source_url, self.fit.source_url)
        self.assertEqual(fill_from_shared_base(self.target, self.user)['offers'], 0)
        self.other.refresh_from_db()
        self.assertEqual(self.other.cost, 2000)
        self.assertEqual(self.other.kaspi_price, 5000)
        self.assertEqual(self.other.stock, '3')
        self.assertTrue(self.other.characteristics['kaspi_identity']['confirmed'])

    def test_brand_suffix_and_punctuation_cannot_match(self):
        for brand, article in [('OTHER', '2028-77K'), ('MOTORAD','2028-77'), ('MOTORAD','202877K')]:
            Offer.objects.create(supplier=self.target, brand=brand, article=article, name='Different', cost=10)
        fill_from_shared_base(self.target, self.user)
        self.assertEqual(VehicleFitment.objects.filter(offer__supplier=self.target).count(), 1)

    def test_unverified_and_conflicting_sources_are_skipped(self):
        self.fit.verified = False; self.fit.save()
        self.assertEqual(fill_from_shared_base(self.target, self.user)['offers'], 0)
        self.fit.verified = True; self.fit.save()
        third = Offer.objects.create(supplier=Supplier.objects.create(name='Phaeton'), brand=self.offer.brand, article=self.offer.article, name='Thermostat', cost=10)
        VehicleFitment.objects.create(offer=third, make='NISSAN', model='OTHER', source_url=self.fit.source_url, verified=True)
        self.assertEqual(fill_from_shared_base(self.target, self.user)['conflicts'], 1)
        self.assertFalse(self.other.fitments.exists())

    def test_staff_review_is_preserved(self):
        VehicleFitment.objects.create(offer=self.other, make='NISSAN', model='REVIEW', source_url=self.fit.source_url, verified=False)
        self.assertEqual(fill_from_shared_base(self.target, self.user)['offers'], 0)
        self.assertEqual(self.other.fitments.get().model, 'REVIEW')

    def payload(self, **kw):
        return StringIO(json.dumps({'checked_on':'2026-11-10','offers':[{'brand':'MOTORAD', 'article':'2028-77K', 'source_article':'2028-77K', 'source_url':self.fit.source_url, 'status':'sourced', 'rows':[{'make':'NISSAN','model':'PRIMERA','years':'1996','engine':'SR20DE'}], **kw}]}))

    def test_catalogue_import_preserves_trade_data_and_filters_years(self):
        self.assertEqual(import_catalogue_fitments(self.payload(), self.target.name, date(2026,11,10), self.user), 1)
        self.assertEqual(self.other.fitments.get().configurations[0]['years'], '1996')
        self.assertEqual(import_catalogue_fitments(self.payload(), self.target.name, date(2026,11,10), self.user), 0)
        self.other.refresh_from_db(); self.assertEqual(self.other.cost, 2000)
        for kw in [{'source_article':'2028-77'}, {'source_url':'https://example.com/fake'}, {'status':'text_needs_review'}]:
            with self.assertRaises(ValueError):
                import_catalogue_fitments(self.payload(**kw), self.target.name, date(2026,11,10), self.user)

    def test_refresh_requires_staff_role_and_post(self):
        url = f'/office/procurement/fitments/refresh/?supplier={self.target.pk}'
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        plain = get_user_model().objects.create_user(username='plain')
        self.client.force_login(plain)
        self.assertEqual(self.client.post(url).status_code, 403)
