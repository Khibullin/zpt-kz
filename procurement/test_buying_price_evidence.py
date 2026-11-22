"""Evidence gate tests. Retaining a product is not the same as approving a purchase."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (Supplier, Offer, PermanentBuyingItem,
                     KaspiBuyingCandidate, Selection, Membership)
from .purchase_readiness import blocked_permanent_offer_ids


class BuyingPriceEvidenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='price_evidence_tester', password='test-only')
        supplier = Supplier.objects.create(name='Omega')
        self.offer = Offer.objects.create(supplier=supplier, brand='WXQP',
            article='51331', name='Рулевая рейка', cost=Decimal('53214'), stock='4')
        self.saved = PermanentBuyingItem.objects.create(
            offer=self.offer, source='apify', product_id='175080373',
            title='WXQP 51331', cost_snapshot=Decimal('53214'),
            kaspi_price_snapshot=Decimal('145000'),
            commission_snapshot=Decimal('12.50'),
            expenses_snapshot=Decimal('250'),
            profit_snapshot=Decimal('73411'),
            roi_snapshot=Decimal('137.9543'), identity_status='candidate')
        self.candidate = KaspiBuyingCandidate.objects.create(
            product_id='175080373', title='WXQP 51331', brand='WXQP',
            minimum_price=Decimal('145000'), offer=self.offer,
            city_offers=[{'city': 'Алматы', 'sellers': []}],
            match_status='candidate')

    def test_bare_minprice_is_not_sufficient(self):
        self.assertEqual(blocked_permanent_offer_ids({self.offer.id}), {self.offer.id})

    def test_merchant_offer_alone_does_not_verify_execution(self):
        self.candidate.city_offers = [
            {'city': 'Алматы', 'sellers': [{'name': 'Продавец', 'price': '145000'}]}]
        self.candidate.save()
        self.assertEqual(blocked_permanent_offer_ids({self.offer.id}), {self.offer.id})

    def test_verified_identity_and_matching_seller_price_pass(self):
        self.candidate.city_offers = [
            {'city': 'Алматы', 'sellers': [{'name': 'Продавец', 'price': '145000'}]}]
        self.candidate.save()
        self.saved.identity_status = 'verified'
        self.saved.save()
        self.assertEqual(blocked_permanent_offer_ids({self.offer.id}), set())

    def test_old_selections_remain_untouched(self):
        Selection.objects.create(user=self.user, offer=self.offer, quantity=2)
        self.assertTrue(Selection.objects.filter(user=self.user, offer=self.offer).exists())
        self.assertEqual(blocked_permanent_offer_ids({self.offer.id}), {self.offer.id})

    def _verify_with_seller(self):
        self.candidate.city_offers = [
            {'city': 'Алматы', 'sellers': [{'name': 'Продавец', 'price': '145000'}]}]
        self.candidate.save(update_fields=['city_offers'])
        self.saved.identity_status = 'verified'
        self.saved.save(update_fields=['identity_status'])

    def test_supplier_stock_must_still_be_positive_before_purchase(self):
        self._verify_with_seller()
        self.offer.stock = '0'
        self.offer.save(update_fields=['stock'])
        self.assertEqual(blocked_permanent_offer_ids({self.offer.pk}), {self.offer.pk})

    def test_supplier_price_change_requires_fresh_profit_review(self):
        self._verify_with_seller()
        self.offer.cost = Decimal('70000')
        self.offer.save(update_fields=['cost'])
        self.assertEqual(blocked_permanent_offer_ids({self.offer.pk}), {self.offer.pk})

    def test_single_selection_rejects_unverified_item_without_mutating_basket(self):
        Membership.objects.create(user=self.user, role='admin')
        self.client.force_login(self.user)
        response = self.client.post(reverse('procurement:select', args=[self.offer.pk]), {'quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Selection.objects.filter(user=self.user, offer=self.offer).exists())

    def test_selection_accepted_only_after_evidence_and_stock(self):
        Membership.objects.create(user=self.user, role='admin')
        self._verify_with_seller()
        self.client.force_login(self.user)
        response = self.client.post(reverse('procurement:select', args=[self.offer.pk]), {'quantity': 1})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Selection.objects.filter(user=self.user, offer=self.offer).exists())
