"""Cumulative procurement list: no expiry, immutable old result, explicit dates."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from procurement.models import Offer, Preferences, Supplier, ProfitableOfferRecord, PriceUpdate
from procurement.opportunities import capture_profitable_offer
from procurement.catalog_paging import cumulative_profitable_query


class CumulativeProfitableTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            'procurement-owner', 'owner@example.test', 'test-password'
        )
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name='Omega')
        self.preferences = Preferences.objects.create(
            user=self.user, commission=Decimal('12.50'),
            expenses=Decimal('250.00'), best_min_profit=Decimal('1000.00')
        )
        self.past = timezone.localdate() - timedelta(days=21)

    def offer(self, article, **overrides):
        values = {
            'supplier': self.supplier, 'brand': 'TEST', 'article': article,
            'name': 'Рычаг ' + article, 'category': 'Подвеска',
            'cost': Decimal('3000.00'), 'stock': '6',
            'kaspi_price': Decimal('7000.00'), 'match_verified': True,
            'supplier_checked_on': self.past,
            'kaspi_price_checked_on': self.past,
        }
        values.update(overrides)
        return Offer.objects.create(**values)

    def url(self, **kwargs):
        return reverse('procurement:dashboard') + '?supplier=' + str(self.supplier.pk) + '&mode=best' + ''.join(
            '&' + key + '=' + str(value) for key,value in kwargs.items()
        )

    def test_old_supplier_and_kaspi_dates_do_not_appear_in_current_buying_list(self):
        old = self.offer('OLD')
        queryset = cumulative_profitable_query(Offer.objects.all(), self.preferences)
        self.assertIn(old.pk, queryset.values_list('pk', flat=True))
        PriceUpdate.objects.create(supplier=self.supplier, user=self.user,
            filename='new.csv', checked_on=timezone.localdate(), sha256='current',
            status='done', rows=[{'brand': 'TEST', 'article': 'NEW'}], row_count=1)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Рычаг OLD')

    def test_history_retains_first_profit_after_new_price_makes_unprofitable(self):
        old = self.offer('ARCHIVE')
        self.assertTrue(capture_profitable_offer(old))
        snapshot = ProfitableOfferRecord.objects.get(offer=old)
        frozen_profit = snapshot.source_profit
        old.cost = Decimal('6600.00')
        old.stock = '0'
        old.supplier_checked_on = timezone.localdate()
        old.save(update_fields=['cost', 'stock', 'supplier_checked_on'])
        self.assertEqual(ProfitableOfferRecord.objects.get(offer=old).source_profit, frozen_profit)
        response = self.client.get(self.url())
        self.assertNotContains(response, 'Рычаг ARCHIVE')
        self.assertNotContains(response, 'История — не для новой закупки')

    def test_new_current_item_without_history_is_visible(self):
        new = self.offer('ADDED', supplier_checked_on=timezone.localdate())
        response = self.client.get(self.url(best_scope='current'))
        self.assertContains(response, new.article)
        response = self.client.get(self.url(best_scope='new'))
        self.assertNotContains(response, 'История — не для новой закупки')

    def test_bearing_not_shown_as_new_candidate(self):
        self.offer('BEARING', name='Подшипник TEST')
        response = self.client.get(self.url())
        self.assertNotContains(response, 'Подшипник TEST')
