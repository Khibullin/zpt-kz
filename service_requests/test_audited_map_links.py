from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.test import TestCase, SimpleTestCase
from core.services.seller_card_links import validate_card_url
from service_requests.models import ServiceSeller

batch = import_module('service_requests.migrations.0017_fix_audited_map_links')


class AuditedMapManifestTests(SimpleTestCase):
    def test_41_existing_2gis_urls_are_kept_without_search_or_new_destinations(self):
        self.assertEqual(len(batch.CORRECTIONS), 41)
        self.assertEqual(len({pk for pk, _, _ in batch.CORRECTIONS}), 41)
        for pk, old, new in batch.CORRECTIONS:
            url, error = validate_card_url('two_gis', new)
            self.assertFalse(error, pk)
            self.assertEqual(url, new)
            self.assertIn(new, old)
            self.assertNotEqual(old, new)


class AuditedMapCorrectionTests(TestCase):
    def test_guarded_idempotent_reversible_map_update_and_visible_link(self):
        pk, old, new = batch.CORRECTIONS[0]
        shop, _ = ServiceSeller.objects.update_or_create(pk=pk, defaults={
            'name':'Тестовое СТО', 'whatsapp':'77012345678', 'city':'Алматы',
            'seller_type':'sto', 'is_active':True, 'is_paused':False,
            'is_test_seller':False, 'map_link':old})
        original = ServiceSeller.objects.filter(pk=pk).values().get()
        page = self.client.get(f'/catalog/services/{pk}/')
        self.assertNotContains(page, 'href="' + new + '"')
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        self.assertEqual(batch.apply_corrections(apps, editor), 1)
        self.assertEqual(batch.apply_corrections(apps, editor), 0)
        self.assertContains(self.client.get(f'/catalog/services/{pk}/'), 'href="' + new + '"')
        updated = ServiceSeller.objects.filter(pk=pk).values().get()
        original['map_link'] = new
        self.assertEqual(updated, original)
        self.assertEqual(batch.apply_corrections(apps, editor, reverse=True), 1)
        shop.refresh_from_db()
        self.assertEqual(shop.map_link, old)
        # A changed value must survive both forward application and rollback.
        shop.map_link = 'https://2gis.kz/almaty/firm/70000001000000000'
        shop.save(update_fields=['map_link'])
        self.assertEqual(batch.apply_corrections(apps, editor), 0)
        self.assertEqual(batch.apply_corrections(apps, editor, reverse=True), 0)
        shop.refresh_from_db()
        self.assertEqual(shop.map_link, 'https://2gis.kz/almaty/firm/70000001000000000')
