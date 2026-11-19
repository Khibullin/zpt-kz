"""Evidence gate tests. Retaining a product is not the same as approving a purchase."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (Supplier, Offer, PermanentBuyingItem,
                     KaspiBuyingCandidate, Selection)
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
