from importlib import import_module

from django.apps import apps
from django.db import transaction
from django.test import TestCase

from core.models import Seller


class AstanaDirectoryPublicationTests(TestCase):
    def setUp(self):
        self.migration = import_module('core.migrations.0098_publish_astana_directory')
        for pk, name, phone in self.migration.SELLERS:
            Seller.objects.create(pk=pk, name=name, whatsapp=phone, city='Астана',
                                  receive_requests=True, sells_new_parts=None)
        self.held = Seller.objects.create(pk=303, name='Авто1000 запчасти',
                                         whatsapp='77013878484', city='Астана')

    def test_only_publication_and_reviewed_name_change_and_rerun_is_safe(self):
        before = {s['id']: s for s in Seller.objects.values()}
        self.assertEqual(self.migration.publish(apps), 24)
        self.assertEqual(self.migration.publish(apps), 0)
        for row in Seller.objects.values():
            previous = before[row['id']]
            differences = {k for k in row if row[k] != previous[k]}
            self.assertLessEqual(differences, {'directory_parts', 'name'})
        self.held.refresh_from_db()
        self.assertFalse(self.held.directory_parts)
        self.assertEqual(Seller.objects.get(pk=594).name, 'Avenue Auto')

    def test_identity_conflict_rolls_back_entire_batch(self):
        Seller.objects.filter(pk=694).update(city='Алматы')
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.migration.publish(apps)
        self.assertFalse(Seller.objects.filter(directory_parts=True).exists())
