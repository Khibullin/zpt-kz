from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from core.models import Seller, Request, BroadcastSettings, RequestDispatch
from core.services.test_receivers import test_receiver_decision
from core.views import _find_matching_sellers, _build_dispatch_queue
from core.request_dispatch_service import seller_allowed_for_dispatch, send_single_dispatch
from service_requests.models import ServiceSeller, Service, ServiceRequest, ServiceBroadcastSettings, ServiceRequestDispatch
from service_requests.services.dispatch_policy import select_service_sellers_for_request
from service_requests.services.whatsapp_dispatch import enqueue_service_request_dispatches, process_due_service_request_dispatches


class UniversalTestReceiverTests(TestCase):
    def setUp(self):
        self.since = timezone.now() - timedelta(minutes=1)
        self.test = Seller.objects.create(name='Owner', whatsapp='77011112233', city='Алматы', transport_type='car', is_test_seller=True, receive_requests=True, test_all_requests_since=self.since)
        self.normal = Seller.objects.create(name='Ordinary', whatsapp='77011112244', city='Алматы', transport_type='car', receive_requests=True, sells_new_parts=True, all_categories=True, all_countries=True, all_brands=True, all_models=True)
        self.settings = BroadcastSettings.load(); self.settings.mode='test'; self.settings.emergency_stop=False; self.settings.save()
        self.req = Request.objects.create(transport_type='truck', brand='Неизвестная марка', model='Модель', category='Двигатель', city='Атырау', phone='77010001122', source='home_short', search_scope='city', is_dismantler_request=True)
        self.service = Service.objects.create(name='Новая услуга')
        self.tech = ServiceSeller.objects.create(name='Test', whatsapp='77011112233', city='Алматы', seller_type='sto', is_test_seller=True, receive_requests=True, test_all_requests_since=self.since)
        ServiceBroadcastSettings.objects.create(mode='test')
        self.sreq = ServiceRequest.objects.create(service_type='detailing', transport_type='truck', city='Атырау', district='Центр', brand='Unknown', model='Unknown', phone='77010001122')
        self.sreq.services.add(self.service)

    def test_all_parts_directions_scopes_transports(self):
        for used in (True,False):
            for scope in ('city','custom','kazakhstan'):
                for transport in ('car','truck'):
                    self.req.is_dismantler_request=used;self.req.search_scope=scope;self.req.selected_cities='Астана';self.req.transport_type=transport
                    self.assertEqual(list(_find_matching_sellers(self.req)[0]),[self.test])
                    self.assertTrue(seller_allowed_for_dispatch(self.test,self.settings,self.req))
                    self.assertEqual(len(_build_dispatch_queue(self.req,[self.test])),1)
        with patch('core.views.send_whatsapp_template',return_value={'ok':True,'message_id':'fake'}) as sender:
            send_single_dispatch(RequestDispatch.objects.get(request=self.req,seller=self.test))
            sender.assert_called_once()

    def test_service_both_directions_and_other_city_district(self):
        for kind in ('sto','detailing'):
            self.sreq.service_type=kind
            self.assertEqual(select_service_sellers_for_request(self.sreq),[self.tech])
        # A same-district ordinary provider cannot suppress universal test contacts in LIVE.
        normal=ServiceSeller.objects.create(name='Local',whatsapp='77011112255',city='Атырау',district='Центр',seller_type='detailing')
        normal.services.add(self.service)
        self.sreq.service_type='detailing'
        ServiceBroadcastSettings.objects.update(mode='live')
        self.assertEqual(set(select_service_sellers_for_request(self.sreq)),{self.tech,normal})
        self.tech.district='Центр';self.tech.save()
        normal.district='Другой';normal.save()
        self.assertEqual(set(select_service_sellers_for_request(self.sreq)),{self.tech,normal})
        enqueue_service_request_dispatches(self.sreq,[self.tech])
        with patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message',return_value={'ok':True,'message_id':'fake'}) as sender:
            result=process_due_service_request_dispatches();self.assertEqual(result['sent'],1);sender.assert_called_once()

    def test_historical_requests_are_not_queued_or_sent(self):
        old=self.since-timedelta(seconds=1)
        Request.objects.filter(pk=self.req.pk).update(created_at=old)
        self.req.refresh_from_db()
        self.assertEqual(list(_find_matching_sellers(self.req)[0]),[])
        self.assertEqual(_build_dispatch_queue(self.req,[self.test]),[])
        self.assertFalse(seller_allowed_for_dispatch(self.test,self.settings,self.req))
        dispatch=RequestDispatch.objects.create(request=self.req,seller=self.test,wave_number=1,position_number=1,scheduled_at=timezone.now())
        with patch('core.views.send_whatsapp_template') as sender:
            self.assertFalse(send_single_dispatch(dispatch)['ok']);sender.assert_not_called()
        ServiceRequest.objects.filter(pk=self.sreq.pk).update(created_at=old);self.sreq.refresh_from_db()
        self.assertEqual(select_service_sellers_for_request(self.sreq),[])
        self.assertEqual(enqueue_service_request_dispatches(self.sreq,[self.tech]),[])
        ServiceRequestDispatch.objects.create(request=self.sreq,seller=self.tech,position_number=1)
        with patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message') as sender:
            self.assertEqual(process_due_service_request_dispatches()['skipped'],1);sender.assert_not_called()

    def test_modes_and_stops_still_apply(self):
        for changes in [{'mode':'off'},{'emergency_stop':True}]:
            for key,value in changes.items():setattr(self.settings,key,value)
            self.settings.save()
            self.assertEqual(list(_find_matching_sellers(self.req)[0]),[])
            self.assertFalse(seller_allowed_for_dispatch(self.test,self.settings,self.req))
            self.settings.mode='test';self.settings.emergency_stop=False
        self.settings.save()
        for field in ('is_paused','receive_requests','is_active'):
            setattr(self.test,field,field=='is_paused');self.test.save()
            self.assertEqual(list(_find_matching_sellers(self.req)[0]),[])
            setattr(self.test,field,field!='is_paused');self.test.save()
        ServiceBroadcastSettings.objects.update(mode='off')
        self.assertEqual(select_service_sellers_for_request(self.sreq),[])

    def test_ordinary_cards_cannot_gain_universal_scope(self):
        self.normal.test_all_requests_since=self.since;self.normal.save()
        self.settings.mode='live';self.settings.save()
        self.assertIsNone(test_receiver_decision(self.normal,self.req))
        self.assertNotIn(self.normal,list(_find_matching_sellers(self.req)[0]))
        self.assertFalse(seller_allowed_for_dispatch(self.normal,self.settings,self.req))
        self.test.is_test_seller=False;self.test.save()
        self.assertNotIn(self.test,list(_find_matching_sellers(self.req)[0]))

    def test_universal_contacts_do_not_short_circuit_ordinary_fallback(self):
        self.settings.mode='live'; self.settings.save()
        self.req.city='Алматы';self.req.transport_type='car';self.req.is_dismantler_request=False;self.req.search_scope='city'
        self.normal.all_brands=False;self.normal.all_models=False;self.normal.brand='Other';self.normal.model='Other';self.normal.save()
        self.test.sells_new_parts=True;self.test.all_brands=True;self.test.all_categories=True;self.test.all_models=True;self.test.all_countries=True;self.test.save()
        self.assertEqual(set(_find_matching_sellers(self.req)[0]),{self.test,self.normal})

    def test_normalized_test_phone_is_not_added_twice(self):
        Seller.objects.create(name='Duplicate', whatsapp='+7 (701) 111-22-33', city='Астана', transport_type='car', is_test_seller=True, receive_requests=True, test_all_requests_since=self.since)
        self.assertEqual(list(_find_matching_sellers(self.req)[0]), [self.test])

    def test_data_migration_only_test_cards_and_unique_companions(self):
        snapshot=Seller.objects.filter(pk=self.normal.pk).values().get()
        self.test.test_all_requests_since=None;self.test.is_paused=True;self.test.receive_requests=False;self.test.save()
        extra=Seller.objects.create(name='Owner2',whatsapp='77011112266',city='Семей',transport_type='car',is_test_seller=True)
        ordinary_service=ServiceSeller.objects.create(name='Ordinary',whatsapp='77011112277',city='Астана',seller_type='sto',receive_requests=False)
        service_snapshot=ServiceSeller.objects.filter(pk=ordinary_service.pk).values().get()
        migration=import_module('core.migrations.0122_enable_owner_test_receivers')
        editor=SimpleNamespace(connection=connection)
        migration.enable_owner_test_receivers(apps,editor)
        migration.enable_owner_test_receivers(apps,editor)
        self.assertEqual(Seller.objects.filter(pk=self.normal.pk).values().get(),snapshot)
        self.assertEqual(ServiceSeller.objects.filter(pk=ordinary_service.pk).values().get(),service_snapshot)
        self.test.refresh_from_db();extra.refresh_from_db()
        self.assertTrue(self.test.is_dismantler and self.test.sells_new_parts and self.test.receive_requests)
        self.assertFalse(self.test.is_paused)
        self.assertTrue(self.test.all_brands and self.test.all_models and self.test.all_categories and self.test.all_countries)
        self.assertEqual(self.test.request_filters['transport_types'],['car','truck'])
        self.assertEqual(ServiceSeller.objects.filter(whatsapp=extra.whatsapp,is_test_seller=True).count(),1)
        self.assertEqual(RequestDispatch.objects.count(),0)
        self.assertEqual(ServiceRequestDispatch.objects.count(),0)
