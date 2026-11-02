from django.contrib.auth import get_user_model
from django.test import TestCase
from service_requests.models import ServiceSeller
from service_requests.sto_import import apply_rows, MARKER
from django.urls import reverse


class StoImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='importer', password='test-only')
        self.row = dict(id='70000001123456789', name='Авто Тест', city='Кокшетау', address='Улица, 1',
                        phones=['77012345678'], wa=[], services=['Ремонт АКПП'],
                        description='Услуги: Ремонт АКПП.', website='', instagram='',
                        map_link='https://2gis.ru/firm/70000001123456789',
                        notes=MARKER + ' 70000001123456789\nsource')

    def test_preview_does_not_write_and_repeat_is_idempotent(self):
        self.assertEqual(apply_rows([self.row], self.user)[0]['status'], 'created')
        self.assertFalse(ServiceSeller.objects.exists())
        apply_rows([self.row], self.user, dry_run=False)
        self.assertEqual(apply_rows([self.row], self.user, dry_run=False)[0]['status'], 'unchanged')
        seller = ServiceSeller.objects.get()
        self.assertFalse(seller.receive_requests)
        self.assertTrue(seller.catalog_only)
        self.assertEqual(seller.services.get().name, 'Ремонт АКПП')
        self.assertEqual(ServiceSeller.objects.count(), 1)

    def test_shared_phone_does_not_merge_branches(self):
        apply_rows([self.row], self.user, dry_run=False)
        branch = {**self.row, 'id': '70000001123456788', 'address': 'Улица, 2', 'map_link': 'https://2gis.ru/firm/70000001123456788'}
        self.assertEqual(apply_rows([branch], self.user, dry_run=False)[0]['status'], 'shared_phone_review')
        self.assertEqual(ServiceSeller.objects.get().address, 'Улица, 1')

    def test_existing_contact_filters_and_dispatch_preserved(self):
        apply_rows([self.row], self.user, dry_run=False)
        seller = ServiceSeller.objects.get()
        seller.whatsapp = '77098765432'
        seller.receive_requests = True
        seller.request_filters = {'confirmed': True}
        seller.save()
        apply_rows([self.row], self.user, dry_run=False)
        seller.refresh_from_db()
        self.assertEqual(seller.whatsapp, '77098765432')
        self.assertTrue(seller.receive_requests)
        self.assertEqual(seller.request_filters, {'confirmed': True})

    def test_connected_account_is_protected(self):
        apply_rows([self.row], self.user, dry_run=False)
        seller = ServiceSeller.objects.get()
        seller.user = self.user
        seller.description = ''
        seller.save()
        self.assertEqual(apply_rows([self.row], self.user, dry_run=False)[0]['status'], 'protected')
        seller.refresh_from_db()
        self.assertEqual(seller.description, '')

    def test_import_requires_staff_and_model_permissions(self):
        url = reverse('control_panel:sto_import')
        self.assertEqual(self.client.get(url).status_code, 302)
        ordinary = get_user_model().objects.create_user(username='staff-no-permission', is_staff=True)
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Проверить партию')
