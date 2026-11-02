from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse
from .models import Supplier, Offer, Preferences, Membership, KaspiSellerSnapshot

class NoSellerModeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('no-seller-buyer')
        Membership.objects.create(user=self.user)
        self.preferences = Preferences.objects.create(user=self.user, commission=10, expenses=250)
        self.supplier = Supplier.objects.create(name='Supplier')
        self.offer = Offer.objects.create(supplier=self.supplier, brand='B', article='A', name='Confirmed empty', cost=1000, stock='5', match_verified=True, kaspi_url='https://kaspi.kz/shop/p/a-1/')
        self.snapshot = KaspiSellerSnapshot.objects.create(offer=self.offer, source_url=self.offer.kaspi_url, checked_on=timezone.localdate(), complete=True, sellers=[])
        self.client.force_login(self.user)

    def get_results(self, **params):
        return self.client.get(reverse('procurement:dashboard'), {'mode': 'no_sellers', **params})

    def test_verified_empty_snapshot_is_included_and_stale_price_hidden(self):
        Offer.objects.filter(pk=self.offer.pk).update(kaspi_price=987654)
        response = self.get_results()
        self.assertEqual(response.context['total'], 1)
        self.assertContains(response, 'Цена продажи не задана')
        self.assertNotContains(response, '987654')
        self.assertContains(response, 'aria-current="page"')

    def test_unknown_incomplete_stale_wrong_city_or_source_never_qualify(self):
        for changes in ({'complete': False}, {'checked_on': timezone.localdate()-timedelta(days=8)}, {'checked_on': timezone.localdate()+timedelta(days=1)}, {'city': 'other'}, {'source_url': 'https://kaspi.kz/shop/p/other-2/'}):
            defaults = dict(complete=True, checked_on=timezone.localdate(), city='750000000', source_url=self.offer.kaspi_url)
            defaults.update(changes)
            KaspiSellerSnapshot.objects.filter(pk=self.snapshot.pk).update(**defaults)
            self.assertEqual(self.get_results().context['total'], 0, changes)

    def test_no_card_is_not_inferred_from_missing_link(self):
        self.assertEqual(self.get_results(kaspi_presence='no_card').context['total'], 0)
        Offer.objects.filter(pk=self.offer.pk).update(kaspi_url='')
        self.assertEqual(self.get_results().context['total'], 0)

    def test_settings_are_separate_and_validate_bounds(self):
        response = self.client.post(reverse('procurement:settings'), {'commission': 10, 'expenses': 250, 'difference_min_profit': 500, 'best_min_profit': 1500, 'no_sellers_days': 3})
        self.assertEqual(response.status_code, 302)
        self.preferences.refresh_from_db()
        self.assertEqual(self.preferences.best_min_profit, Decimal(1500))
        self.assertEqual(self.preferences.no_sellers_days, 3)
