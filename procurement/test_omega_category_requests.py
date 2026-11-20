"""Omega staged category requests must preserve the current buying workflow."""
import json
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Activity, Membership, Offer, PriceUpdate, Selection, Supplier
from .omega_selection_requests import (
    REQUEST_ACTION, DEFAULT_CATEGORIES, price_categories, request_queries,
    save_request,
)


class OmegaCategoryRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('omega-categories-admin')
        Membership.objects.create(user=cls.user, role='admin')
        cls.omega = Supplier.objects.create(name='Omega')
        cls.offer = Offer.objects.create(
            supplier=cls.omega, brand='SRR', article='EXISTING',
            name='Existing offer', category='Other',
            cost=Decimal('1500'), stock='6')
        Selection.objects.create(user=cls.user, offer=cls.offer, quantity=2)
        cls.price = PriceUpdate.objects.create(
            supplier=cls.omega, user=cls.user,
            filename='solid-2026.xlsx', sha256='mocksha256', checked_on=date.today(),
            status='ready', row_count=4,
            rows=[
                {'brand': 'SRR', 'article': '085066', 'name': 'Амортизатор 1',
                 'category': 'Амортизатор', 'cost': '2000', 'stock': '20>'},
                {'brand': 'SRR', 'article': '85066', 'name': 'Амортизатор 2',
                 'category': 'Амортизатор', 'cost': '2100', 'stock': '5'},
                {'brand': 'WXQP', 'article': 'B-10', 'name': 'Рычаг',
                 'category': 'Рычаг', 'cost': '3100', 'stock': '3'},
                {'brand': 'SRR', 'article': 'NOT', 'name': 'Нет',
                 'category': 'Амортизатор', 'cost': '5000', 'stock': '0'},
            ])

    def setUp(self):
        self.client.force_login(self.user)

    def test_categories_list_counts_while_preserving_offers(self):
        summary = {x['name']: x for x in price_categories(self.price)}
        self.assertEqual(summary['Амортизатор']['total'], 3)
        self.assertEqual(summary['Амортизатор']['eligible'], 2)
        self.assertEqual(summary['Рычаг']['eligible'], 1)
        response = self.client.get(reverse('procurement:selection_rules'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Выбор категорий Омеги')
        self.assertContains(response, 'История запросов')
        self.assertContains(response, 'solid-2026.xlsx')
        self.assertContains(response, 'value="Амортизатор"')
        self.offer.refresh_from_db()
        self.assertEqual((self.offer.cost, self.offer.stock), (Decimal('1500'), '6'))

    def test_independent_history_saved_and_exported_without_changes_to_purchase(self):
        url = reverse('procurement:selection_rules')
        response = self.client.post(url, {'action': 'save_omega_request',
            'price_batch_id': self.price.pk,
            'omega_category': ['Амортизатор', 'Рычаг'],
            'omega_min_cost': '2000'})
        self.assertEqual(response.status_code, 302)
        first = Activity.objects.get(action=REQUEST_ACTION)
        self.assertEqual(first.details['title'], 'Запрос №1')
        self.assertEqual(first.details['total'], 3)
        self.assertEqual(first.details['query_count'], 3)
        second = save_request(batch_id=self.price.pk, categories=['Рычаг'],
                              minimum=3000, user=self.user)
        self.assertEqual(second.details['title'], 'Запрос №2')
        self.assertEqual(second.details['total'], 1)
        self.assertEqual(first.details['min_cost'], '2000')
        self.assertEqual(second.details['min_cost'], '3000')
        export = self.client.get(reverse('procurement:selection_apify_json'),
                                 {'request_id': first.pk, 'batch': 1})
        self.assertEqual(export.status_code, 200)
        data = json.loads(export.content)
        self.assertEqual(len(data['searchQueries']), 3)
        self.assertEqual(data['cities'][-1], '391010000')
        mapping = self.client.get(reverse('procurement:selection_apify_json'),
                                  {'request_id': first.pk, 'batch': 1, 'format': 'mapping'})
        self.assertEqual(mapping.status_code, 200)
        self.assertEqual(len(json.loads(mapping.content)['mapping']), 3)
        self.assertEqual(list(Selection.objects.values_list('offer_id', 'quantity')),
                         [(self.offer.pk, 2)])
        self.offer.refresh_from_db()
        self.assertEqual((self.offer.cost, self.offer.stock), (Decimal('1500'), '6'))

    def test_invalid_selection_does_not_save_anything(self):
        response = self.client.post(reverse('procurement:selection_rules'), {
            'action': 'save_omega_request', 'price_batch_id': self.price.pk,
            'omega_category': ['Случайная категория'], 'omega_min_cost': '2000'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Activity.objects.filter(action=REQUEST_ACTION).exists())
