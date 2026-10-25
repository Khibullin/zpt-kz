from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.test import TestCase
from service_requests.models import ServiceSeller

batch = import_module('service_requests.migrations.0018_verified_address_details')


class VerifiedAddressTests(TestCase):
    def test_only_verified_addresses_change_without_city_or_dispatch_changes(self):
        originals = {}
        for pk, city, url, old, new in batch.CORRECTIONS:
            ServiceSeller.objects.update_or_create(pk=pk, defaults={
                'name':f'Организация {pk}', 'whatsapp':f'770900{pk:05}',
                'city':city, 'map_link':url, 'address':old,
                'seller_type':'sto', 'receive_requests':False})
            originals[pk] = ServiceSeller.objects.filter(pk=pk).values().get()
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        self.assertEqual(batch.apply_corrections(apps, editor), 5)
        self.assertEqual(batch.apply_corrections(apps, editor), 0)
        for pk, city, url, old, new in batch.CORRECTIONS:
            expected = {**originals[pk], 'address':new}
            self.assertEqual(ServiceSeller.objects.filter(pk=pk).values().get(), expected)
        self.assertEqual(batch.apply_corrections(apps, editor, reverse=True), 5)
        for pk in originals:
            self.assertEqual(ServiceSeller.objects.filter(pk=pk).values().get(), originals[pk])
        # A manually changed city or address is not overwritten.
        ServiceSeller.objects.filter(pk=204).update(address='Новый адрес владельца')
        ServiceSeller.objects.filter(pk=347).update(city='Уточнённый населённый пункт')
        self.assertEqual(batch.apply_corrections(apps, editor), 3)
        self.assertEqual(ServiceSeller.objects.get(pk=204).address,'Новый адрес владельца')
        self.assertEqual(ServiceSeller.objects.get(pk=347).city,'Уточнённый населённый пункт')
