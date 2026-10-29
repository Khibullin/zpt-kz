from decimal import Decimal
from types import SimpleNamespace
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from .supplier_terms import supplier_terms

class SupplierTermsTests(SimpleTestCase):
    def setUp(self):
        self.omega = SimpleNamespace(name='Omega', website='https://omega-auto-parts.kz/')

    def test_budget_and_supplier_isolation(self):
        terms = supplier_terms(self.omega, Decimal('420000'))
        self.assertEqual((terms['rate'], terms['remaining']), (0, Decimal('80000')))
        self.assertIsNone(supplier_terms(SimpleNamespace(name='Other', website='https://other.kz'), 5000000))

    def test_all_tiers_and_boundaries(self):
        terms = supplier_terms(self.omega)
        for threshold, rate, label in terms['bands'][1:]:
            self.assertTrue(supplier_terms(self.omega, threshold)['boundary'])
            self.assertEqual(supplier_terms(self.omega, Decimal(threshold)+Decimal('.01'))['rate'], rate)
        self.assertNotIn('remaining', supplier_terms(self.omega, 31000000))

    def test_render_boundary_and_reference_only(self):
        html = render_to_string('procurement/supplier_terms.html', {'terms': supplier_terms(self.omega, 500000)})
        self.assertIn('на границе ступеней', html)
        self.assertIn('автоматически не изменяются', html)
        self.assertNotIn('Справочная скидка по шкале:', html)

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Supplier, Membership, Offer, Selection

class SupplierTermsViewsTests(TestCase):
    def test_separate_supplier_budgets_and_protected_card(self):
        user = get_user_model().objects.create_user('terms-buyer', password='local-test-pass')
        Membership.objects.create(user=user)
        omega = Supplier.objects.create(name='Omega')
        other = Supplier.objects.create(name='Other')
        for supplier, cost in [(omega, '420000'), (other, '1000000')]:
            offer = Offer.objects.create(supplier=supplier, brand='Brand', article='A', name='Part', cost=cost)
            Selection.objects.create(user=user, offer=offer, quantity=1)
        self.assertEqual(self.client.get(reverse('procurement:supplier_detail', args=[omega.pk])).status_code, 302)
        self.client.force_login(user)
        response = self.client.get(reverse('procurement:basket'))
        self.assertEqual(response.status_code, 200)
        groups = response.context['supplier_groups']
        by_supplier = {g['supplier'].pk: g for g in groups}
        self.assertEqual(by_supplier[omega.pk]['terms']['remaining'], Decimal('80000'))
        self.assertIsNone(by_supplier[other.pk]['terms'])
        self.assertContains(self.client.get(reverse('procurement:supplier_detail', args=[omega.pk])), 'Свыше 30 млн')
        self.assertContains(self.client.get(reverse('procurement:supplier_detail', args=[other.pk])), 'пока не добавлены')
