"""Isolated UI regression for matched Kaspi buying cards (no production writes)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    KaspiBuyingCandidate, Membership, Offer, Preferences,
    Selection, Supplier,
)


class MatchedBuyingPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('buying-page-check')
        Membership.objects.create(user=cls.user, role='sales')
        Preferences.objects.create(
            user=cls.user, commission=Decimal('12.5'), expenses=Decimal('250'),
        )
        cls.supplier = Supplier.objects.create(name='Buying regression supplier')
        cls.offer = Offer.objects.create(
            supplier=cls.supplier, brand='BR', article='TEST-1',
            name='Filter', applicability='Toyota Camry', cost=Decimal('1000'),
            stock='20', min_order=2, photo_url='https://example.com/part.jpg',
        )
        KaspiBuyingCandidate.objects.bulk_create([
            KaspiBuyingCandidate(
                product_id=f'kaspi-regression-{index}',
                title=f'Kaspi matched card {index}', brand='BR',
                url=f'https://kaspi.kz/shop/p/regression-{index}/',
                minimum_price=Decimal('500') if index == 44
                else Decimal(3000 + index),
                city_offers=[{'sellers_count': 2}],
                offer=cls.offer, match_status='candidate',
            ) for index in range(45)
        ])
        KaspiBuyingCandidate.objects.create(
            product_id='unmatched-regression', title='Unmatched',
            url='https://kaspi.kz/shop/p/unmatched-regression/',
            match_status='unmatched',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def dashboard(self, **query):
        return self.client.get(
            reverse('procurement:dashboard'),
            {'mode': 'best', 'supplier': str(self.supplier.pk), **query},
        )

    def test_every_matched_card_is_counted_and_last_page_keeps_losses(self):
        first = self.dashboard()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.context['total'], 45)
        self.assertEqual(first.context['apify_in_best'], 45)
        self.assertEqual(len(first.context['page'].object_list), 40)
        self.assertContains(first, 'Карточка Kaspi')
        self.assertContains(first, 'Выбрать')
        self.assertContains(first, 'Последняя')
        last = self.dashboard(page=2)
        self.assertEqual(last.status_code, 200)
        self.assertEqual(len(last.context['page'].object_list), 5)
        self.assertContains(last, 'Убыток:')
        self.assertContains(last, 'regression-44')
        self.assertContains(last, 'Карточка Kaspi: Kaspi matched card 44')
        self.assertEqual(first.context['page'].paginator.num_pages, 2)

    def test_full_3961_card_batch_has_100_pages_with_last_card(self):
        KaspiBuyingCandidate.objects.bulk_create([
            KaspiBuyingCandidate(
                product_id=f'bulk-{index}',
                title=f'Batch card {index}', brand='BR',
                url=f'https://kaspi.kz/shop/p/bulk-{index}/',
                minimum_price=Decimal('4000') + index,
                city_offers=[{'sellers_count': 2}],
                offer=self.offer, match_status='candidate',
            ) for index in range(3916)
        ], batch_size=500)
        first = self.dashboard()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.context['total'], 3961)
        self.assertEqual(first.context['apify_in_best'], 3961)
        self.assertEqual(first.context['page'].paginator.num_pages, 100)
        self.assertContains(first, 'Карточка Kaspi: Batch card')
        last = self.dashboard(page=100)
        self.assertEqual(last.status_code, 200)
        self.assertEqual(len(last.context['page'].object_list), 1)
        self.assertContains(last, 'regression-44')
        self.assertContains(last, 'Убыток:')

    def test_filters_search_fitment_and_seller_bounds_without_losing_cards(self):
        self.assertEqual(self.dashboard(q='Camry').context['total'], 45)
        self.assertEqual(self.dashboard(q='unknown-car').context['total'], 0)
        self.assertEqual(
            self.dashboard(seller_mode='exact', kaspi_seller_count='2').context['total'],
            45,
        )
        self.assertEqual(
            self.dashboard(seller_mode='exact', kaspi_seller_count='3').context['total'],
            0,
        )
        self.assertEqual(self.dashboard(min_profit='1000').context['total'], 44)

    def test_bulk_add_uses_unique_supplier_offer_and_preserves_quantity(self):
        url = reverse('procurement:select_many')
        payload = {'offer_ids': [str(self.offer.pk)]}
        for _ in range(2):
            result = self.client.post(url, payload, HTTP_ACCEPT='application/json')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['basket_count'], 1)
        selected = Selection.objects.get(user=self.user, offer=self.offer)
        self.assertEqual(selected.quantity, 2)
        self.assertContains(self.client.get(reverse('procurement:basket')), 'TEST-1')
