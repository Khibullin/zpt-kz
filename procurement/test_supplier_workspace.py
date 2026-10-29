from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Membership, Offer, Supplier, VehicleFitment


class SupplierWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('supplier-manager')
        Membership.objects.create(user=cls.user, role='admin')
        cls.omega = Supplier.objects.create(name='Omega')
        cls.valtec = Supplier.objects.create(name='Компания Вальтек')
        cls.a = Offer.objects.create(supplier=cls.omega, brand='A', article='SAME', name='Omega detail', cost=Decimal('100'), category='Кузов')
        cls.b = Offer.objects.create(supplier=cls.valtec, brand='A', article='SAME', name='Valtec detail', cost=Decimal('200'), category='Масляные фильтры')
        VehicleFitment.objects.create(offer=cls.a, make='TOYOTA', model='CAMRY', verified=True)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('procurement:dashboard')

    def test_supplier_isolates_rows_categories_and_fitments(self):
        response = self.client.get(self.url, {'supplier': self.valtec.pk})
        self.assertContains(response, 'Valtec detail')
        self.assertNotContains(response, 'Omega detail')
        self.assertNotContains(response, 'Категории Omega')
        form = response.context['filter_form']
        self.assertEqual(list(form.fields['category'].choices), [('', 'Все категории'), ('Масляные фильтры', 'Масляные фильтры')])
        self.assertEqual(len(form.fields['vehicle_make'].choices), 1)
        self.assertEqual(response.context['supplier_stats'], {'loaded': 1, 'linked': 0, 'priced': 0, 'verified': 0})

    def test_supplier_survives_settings_and_sidebar_return(self):
        self.client.get(self.url, {'supplier': self.valtec.pk})
        response = self.client.get(reverse('procurement:settings'))
        self.assertEqual(response.context['catalog_url'], f'{self.url}?supplier={self.valtec.pk}')
        response = self.client.get(self.url, {'mode': 'best'}, follow=True)
        self.assertEqual(response.context['selected_supplier'], self.valtec)
        self.assertEqual(response.context['mode'], 'best')
        self.assertContains(response, 'загружено <strong>1</strong>', html=False)

    def test_explicit_all_suppliers_clears_session_and_invalid_does_not(self):
        self.client.get(self.url, {'supplier': self.valtec.pk})
        self.assertEqual(self.client.get(self.url, {'supplier': 'bad'}).status_code, 400)
        self.assertEqual(self.client.session['procurement_supplier'], self.valtec.pk)
        self.client.get(self.url, {'supplier': ''})
        self.assertIsNone(self.client.session['procurement_supplier'])
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_csv_and_modes_keep_supplier(self):
        response = self.client.get(self.url, {'supplier': self.valtec.pk, 'download': 'csv'})
        self.assertIn('Valtec detail', response.content.decode('utf-8-sig'))
        self.assertNotIn('Omega detail', response.content.decode('utf-8-sig'))
        response = self.client.get(self.url, {'supplier': self.valtec.pk})
        self.assertContains(response, f'supplier={self.valtec.pk}')
        self.assertEqual(response.context['reset_url'], f'{self.url}?supplier={self.valtec.pk}')

    def test_import_redirects_to_imported_supplier(self):
        file = SimpleUploadedFile('price.csv', b'article,brand,name,cost\nNEW,NGK,Spark plug,500\n')
        response = self.client.post(reverse('procurement:upload'), {'supplier': self.valtec.name, 'kind': 'catalog', 'checked_on': '2026-10-29', 'file': file})
        self.assertRedirects(response, f'{self.url}?supplier={self.valtec.pk}')
        self.assertEqual(Offer.objects.get(supplier=self.valtec, article='NEW').cost, 500)

    def test_other_tab_cannot_change_explicit_upload_supplier(self):
        response = self.client.get(self.url, {'supplier': self.valtec.pk})
        upload_url = response.context['upload_url']
        settings_url = response.context['settings_url']
        basket_url = response.context['basket_url']
        self.client.get(self.url, {'supplier': self.omega.pk})
        response = self.client.get(upload_url)
        self.assertEqual(response.context['form'].initial['supplier'], self.valtec.name)
        self.assertEqual(response.context['selected_supplier'], self.valtec)
        self.assertEqual(response.context['catalog_url'], f'{self.url}?supplier={self.valtec.pk}')
        response = self.client.get(basket_url)
        self.assertEqual(response.context['selected_supplier'], self.valtec)
        response = self.client.get(settings_url)
        self.assertEqual(response.context['selected_supplier'], self.valtec)
        response = self.client.post(settings_url, {'commission': '10.9', 'expenses': '250', 'min_profit': '1000', 'min_markup': '30'})
        self.assertRedirects(response, f'{self.url}?supplier={self.valtec.pk}')

    def test_explicit_all_suppliers_upload_does_not_use_another_tabs_supplier(self):
        self.client.get(self.url, {'supplier': self.omega.pk})
        response = self.client.get(reverse('procurement:upload') + '?supplier=')
        self.assertEqual(response.context['form'].initial['supplier'], '')
        self.assertIsNone(response.context['selected_supplier'])

    def test_product_context_belongs_to_its_supplier(self):
        self.client.get(self.url, {'supplier': self.omega.pk})
        response = self.client.get(reverse('procurement:detail', args=[self.b.pk]))
        self.assertEqual(response.context['selected_supplier'], self.valtec)
