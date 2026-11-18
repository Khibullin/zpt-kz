from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import Membership, Offer, Selection, Supplier


class BulkSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('bulk-manager')
        cls.other = get_user_model().objects.create_user('other-manager')
        cls.outsider = get_user_model().objects.create_user('no-procurement')
        Membership.objects.create(user=cls.user, role='sales')
        cls.supplier = Supplier.objects.create(name='Omega')
        cls.offers = [Offer.objects.create(supplier=cls.supplier, brand='TEST',
            article=str(i), name='Товар ' + str(i), cost=Decimal('3000'),
            min_order=i + 1) for i in range(2)]

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('procurement:select_many')

    def test_adds_checked_items_privately_and_preserves_existing_quantities(self):
        Selection.objects.create(user=self.other, offer=self.offers[0], quantity=9)
        Selection.objects.create(user=self.user, offer=self.offers[0], quantity=7)
        ids = [o.pk for o in self.offers]
        for _ in range(2):
            response = self.client.post(self.url, {'offer_ids': ids + ids}, HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['basket_count'], 2)
            self.assertEqual(response.json()['selected_ids'], ids)
        self.assertEqual(Selection.objects.get(user=self.user, offer=self.offers[0]).quantity, 7)
        self.assertEqual(Selection.objects.get(user=self.user, offer=self.offers[1]).quantity, 2)
        self.assertEqual(Selection.objects.get(user=self.other).quantity, 9)

    def test_invalid_or_missing_items_do_not_partially_add(self):
        for ids in ([], ['bad'], ['0'], [self.offers[0].pk, 99999999], ['1'] * 201, ['9' * 19]):
            response = self.client.post(self.url, {'offer_ids': ids}, HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 400)
            self.assertFalse(Selection.objects.filter(user=self.user).exists())

    def test_preserves_filters_without_javascript_and_rejects_external_return(self):
        target = reverse('procurement:dashboard') + '?supplier=1&mode=best&q=0&page=2'
        response = self.client.post(self.url, {'offer_ids': [self.offers[0].pk], 'next': target})
        self.assertRedirects(response, target, fetch_redirect_response=False)
        response = self.client.post(self.url, {'offer_ids': [self.offers[0].pk], 'next': 'https://other.example/'})
        self.assertRedirects(response, reverse('procurement:basket'), fetch_redirect_response=False)

    def test_post_membership_and_csrf_are_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, {'offer_ids': [self.offers[0].pk]}).status_code, 403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(self.url, {'offer_ids': [self.offers[0].pk]}).status_code, 403)
        self.assertFalse(Selection.objects.exists())

    def test_best_page_is_compact_and_sidebar_links_preserve_supplier(self):
        response = self.client.get(reverse('procurement:dashboard'), {'supplier': self.supplier.pk, 'mode': 'best'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'?supplier={self.supplier.pk}&amp;mode=best')
        self.assertContains(response, 'Товары для Kaspi', count=1)
        self.assertContains(response, '>Все товары</a>', count=1)
        self.assertContains(response, 'data-bulk-selection')
        self.assertContains(response, '<summary class="button">Фильтры</summary>', html=True)
        self.assertNotContains(response, 'compact-tabs"')
        self.assertNotContains(response, 'Не удаётся получить цены Kaspi')
        self.assertNotContains(response, 'best-selection-rules')
