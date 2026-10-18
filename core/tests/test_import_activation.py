import importlib,json
from django.apps import apps
from django.test import TestCase
from core.models import Seller,Brand,Country,Request,RequestDispatch,BroadcastSettings
from core.views import _find_matching_sellers
from service_requests.models import ServiceSeller,Service,ServiceBroadcastSettings
m=importlib.import_module('core.migrations.0096_activate_imported_organisations')

class ImportActivationTests(TestCase):
    def setUp(self):
        self.payload=json.loads(m.PAYLOAD.read_text())
        country=Country.objects.create(name='Test')
        for p in self.payload['plans']:
            Seller.objects.create(id=p['id'],city='Алматы',**p['before'])
            for brand in p['add_brands']:
                Brand.objects.get_or_create(id=brand,defaults={'name':str(brand),'country':country})
        target=self.payload['service']
        ServiceSeller.objects.create(id=204,name=target['name'],whatsapp=target['whatsapp'],city='Талдыкорган',seller_type='sto',receive_requests=False)
        for key,name in target['services'].items():Service.objects.create(id=int(key),name=name)
        ServiceBroadcastSettings.objects.create(mode='test')
        BroadcastSettings.objects.create(mode='live',emergency_stop=False,wave_size=10,wave_interval_minutes=5)

    def test_activation_routing_geography_and_no_replay(self):
        self.assertEqual(m.activate(apps,self.payload),38)
        self.assertEqual(m.activate(apps,self.payload),0)
        self.assertEqual(Request.objects.count(),0)
        self.assertEqual(RequestDispatch.objects.count(),0)
        req=Request(source=Request.SOURCE_HOME_SHORT,transport_type='car',category='Кузов',city='Алматы',search_scope='city',is_dismantler_request=False)
        ids=set(_find_matching_sellers(req)[0].values_list('id',flat=True))
        self.assertIn(905,ids);self.assertNotIn(431,ids)
        req.city='Астана'
        self.assertEqual(_find_matching_sellers(req)[0].count(),0)
        self.assertTrue(Seller.objects.get(pk=431).is_dismantler)
        self.assertIsNone(Seller.objects.get(pk=431).sells_new_parts)
        self.assertFalse(Seller.objects.get(pk=909).all_brands)
        self.assertEqual(list(Seller.objects.get(pk=909).selected_brands.values_list('id',flat=True)),[45])
        self.assertTrue(ServiceSeller.objects.get(pk=204).receive_requests)
        self.assertEqual(ServiceSeller.objects.get(pk=204).services.count(),4)
        self.assertEqual(ServiceBroadcastSettings.objects.get().mode,'test')
        settings=BroadcastSettings.objects.get()
        self.assertEqual((settings.wave_size,settings.wave_interval_minutes),(10,5))

    def test_changed_identity_is_rejected(self):
        Seller.objects.filter(pk=905).update(whatsapp='77000000000')
        with self.assertRaises(RuntimeError):m.activate(apps,self.payload)
        self.assertFalse(Seller.objects.get(pk=905).receive_requests)
