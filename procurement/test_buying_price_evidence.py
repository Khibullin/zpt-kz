"""Evidence gate tests. Retaining a product is not the same as approving a purchase."""
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (Supplier, Offer, PermanentBuyingItem,
                     KaspiBuyingCandidate, Selection, Membership, KaspiPriceAudit)
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

    def _audit(self, **changes):
        defaults = dict(
            offer=self.offer, source_filename='2026-11-22-audit.json',
            min_price=Decimal('145000'), product_id=self.saved.product_id,
            card_url='https://kaspi.kz/shop/p/example-175080373/',
            card_title='WXQP 51331', seller_name='Продавец',
            cost_snapshot=self.offer.cost,
            commission_snapshot=Decimal('12.5'),
            expenses_snapshot=Decimal('250'),
            profit=Decimal('73411'), roi=Decimal('137.9543'),
            identity_status='needs_review')
        defaults.update(changes)
        return KaspiPriceAudit.objects.create(**defaults)

    def test_latest_unverified_audit_blocks_older_verified_identity(self):
        self._verify_with_seller()
        self._audit()
        self.assertEqual(blocked_permanent_offer_ids({self.offer.pk}), {self.offer.pk})

    def test_latest_lower_roi_blocks_even_verified_audit(self):
        self._verify_with_seller()
        self._audit(identity_status='verified', roi=Decimal('49.99'))
        self.assertEqual(blocked_permanent_offer_ids({self.offer.pk}), {self.offer.pk})

    def test_latest_different_price_blocks_even_verified_audit(self):
        self._verify_with_seller()
        self._audit(identity_status='verified', min_price=Decimal('140000'))
        self.assertEqual(blocked_permanent_offer_ids({self.offer.pk}), {self.offer.pk})

    def test_manual_verification_requires_comparison_evidence(self):
        Membership.objects.create(user=self.user, role='admin')
        self.client.force_login(self.user)
        audit = self._audit()
        response = self.client.post(
            reverse('procurement:comparison_review_identity', args=[self.offer.pk]),
            {'source_file': audit.source_filename, 'product_id': audit.product_id,
             'identity_status': 'verified', 'confirm_all': 'yes'})
        self.assertEqual(response.status_code, 400)
        audit.refresh_from_db()
        self.assertEqual(audit.identity_status, 'needs_review')

    def test_manual_mismatch_sets_status_and_preserves_prices(self):
        Membership.objects.create(user=self.user, role='admin')
        self.client.force_login(self.user)
        audit = self._audit()
        response = self.client.post(
            reverse('procurement:comparison_review_identity', args=[self.offer.pk]),
            {'source_file': audit.source_filename, 'product_id': audit.product_id,
             'identity_status': 'mismatch'})
        self.assertEqual(response.status_code, 302)
        audit.refresh_from_db()
        self.assertEqual(audit.identity_status, 'mismatch')
        self.assertEqual(audit.min_price, Decimal('145000'))

    def test_old_seller_evidence_is_not_current_price(self):
        self._verify_with_seller()
        KaspiBuyingCandidate.objects.filter(pk=self.candidate.pk).update(
            imported_at=timezone.now() - timedelta(days=8))
        self.assertEqual(blocked_permanent_offer_ids({self.offer.pk}), {self.offer.pk})

    def test_old_audit_blocks_purchase_even_if_verified(self):
        self._verify_with_seller()
        audit = self._audit(identity_status='verified')
        KaspiPriceAudit.objects.filter(pk=audit.pk).update(
            observed_at=timezone.now() - timedelta(days=8))
        self.assertEqual(blocked_permanent_offer_ids({self.offer.pk}), {self.offer.pk})
