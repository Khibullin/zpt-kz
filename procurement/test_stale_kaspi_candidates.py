"""Historical Kaspi prices stay visible for review, never in confirmed purchase candidates."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from procurement.catalog_paging import best_candidates_query, stale_price_candidates_query
from procurement.models import Offer, Preferences, Supplier


class StaleKaspiCandidateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='stale-check-owner', email='owner@example.test', password='pass'
        )
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name='Фаетон')
        self.today = timezone.localdate()
        self.preferences = Preferences.objects.create(
            user=self.user, commission=Decimal('12.50'),
            expenses=Decimal('250'), best_min_profit=Decimal('1000')
        )

    def offer(self, article, **kwargs):
        payload = dict(
            supplier=self.supplier, brand='TEST', article=article,
            name='Сайлентблок ' + article, category='Подвеска',
            cost=Decimal('1000'), kaspi_price=Decimal('4000'), stock='8',
            match_verified=True, supplier_checked_on=self.today,
            kaspi_price_checked_on=self.today - timedelta(days=8),
        )
        payload.update(kwargs)
        return Offer.objects.create(**payload)

    def test_stale_and_undated_prices_are_review_only(self):
        stale = self.offer('OLD')
        undated = self.offer('NO-DATE', kaspi_price_checked_on=None)
        fresh = self.offer('FRESH', kaspi_price_checked_on=self.today)
        self.offer('NO-STOCK', stock='0')
        self.offer('NOT-VERIFIED', match_verified=False)
        self.offer('NO-PROFIT', kaspi_price=Decimal('1200'))
        candidates = stale_price_candidates_query(
            Offer.objects.all(), self.preferences, self.today
        )
        self.assertEqual(set(candidates.values_list('pk', flat=True)),
                         {stale.pk, undated.pk, self.supplier.offer_set.get(article='NO-STOCK').pk})
        confirmed = best_candidates_query(
            Offer.objects.all(), self.preferences, self.today
        )
        self.assertIn(fresh.pk, list(confirmed.values_list('pk', flat=True)))
        self.assertNotIn(stale.pk, list(confirmed.values_list('pk', flat=True)))

    def test_review_page_disables_purchase_and_does_not_rewrite_dates(self):
        old = self.offer('OLD')
        url = reverse('procurement:dashboard')
        response = self.client.get(url, {
            'supplier': self.supplier.pk, 'mode': 'stale_prices'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Требуется обновить цену Kaspi')
        self.assertContains(response, 'Сначала обновите цену Kaspi')
        self.assertNotContains(response, '+ В закупку')
        old.refresh_from_db()
        self.assertEqual(old.kaspi_price_checked_on, self.today - timedelta(days=8))
        self.assertEqual(old.kaspi_price, Decimal('4000'))
