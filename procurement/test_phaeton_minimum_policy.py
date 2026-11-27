from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Supplier, Offer, KaspiPriceAudit, PermanentBuyingItem, Preferences, Membership, Selection
from .permanent_buying_visible import visible_permanent_buying_rows
from .apify_profitable_publish import eligible_source_audits
from .apify_price_recheck import import_apify_recheck_results


class PhaetonMinimumPolicyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('phaeton-minimum')
        Membership.objects.create(user=self.user, role='admin')
        self.prefs = Preferences.objects.create(user=self.user, commission=Decimal('10'), expenses=250)
        self.offer = Offer.objects.create(supplier=Supplier.objects.create(name='Phaeton DC'),
            brand='PARTS-MALL', article='PSC-B003', name='Ролик', cost=6000, stock='5')
        self.saved = PermanentBuyingItem.objects.create(offer=self.offer, title='Ролик',
            cost_snapshot=6000, kaspi_price_snapshot=20000, commission_snapshot=10,
            expenses_snapshot=250, profit_snapshot=11750, roi_snapshot=195)
        self.audit = KaspiPriceAudit.objects.create(offer=self.offer, source_filename='pilot.json',
            min_price=20000, product_id='153306919', card_title='PARTS-MALL PSC-B003',
            card_url='https://kaspi.kz/shop/p/roller-153306919/', seller_name='shop',
            cost_snapshot=6000, commission_snapshot=10, expenses_snapshot=250, profit=11750, roi=195)

    def rows(self, **params):
        request = SimpleNamespace(user=self.user, GET=params)
        return visible_permanent_buying_rows(request, self.prefs, {'supplier': self.offer.supplier})

    def approve(self):
        self.audit.identity_status = 'verified'
        self.audit.minimum_review_status = 'confirmed'
        self.audit.minimum_reviewed_at = timezone.now()
        self.audit.minimum_review_evidence = dict(scope='kazakhstan', search_complete=True, sellers_complete=True)
        self.audit.save()

    def test_unreviewed_prices_hidden_but_retained_and_reviewable(self):
        Selection.objects.create(user=self.user, offer=self.offer, quantity=3)
        self.assertEqual(self.rows(), [])
        self.assertFalse(eligible_source_audits('pilot.json').exists())
        queue = self.rows(price_review='1')
        self.assertEqual(len(queue), 1)
        self.assertTrue(queue[0]['minimum_review_required'])
        self.assertEqual(Selection.objects.get(offer=self.offer).quantity, 3)
        self.assertTrue(PermanentBuyingItem.objects.filter(pk=self.saved.pk).exists())

    def test_low_roi_stays_in_review_queue(self):
        self.audit.roi = -10
        self.audit.save()
        self.assertEqual(len(self.rows(price_review='1')), 1)

    def test_selected_latest_audit_requires_own_review_and_fresh_date(self):
        self.approve()
        self.assertEqual(len(self.rows()), 1)
        self.audit.minimum_reviewed_at = timezone.now() - timedelta(days=8)
        self.audit.save()
        self.assertEqual(self.rows(), [])
        self.approve()
        self.audit.pk = None
        self.audit.source_filename = 'new-unreviewed.json'
        self.audit.minimum_review_status = 'needs_review'
        self.audit.save()
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.rows(apify_source='pilot.json')), 1)

    def test_almaty_quote_is_not_national_approval(self):
        self.approve()
        self.audit.minimum_review_evidence['scope'] = 'almaty'
        self.audit.save()
        self.assertEqual(self.rows(), [])

    def test_csv_cannot_bypass_gate_and_queue_has_no_purchase_checkbox(self):
        self.client.force_login(self.user)
        url = reverse('procurement:dashboard')
        response = self.client.get(url, dict(mode='best', supplier=self.offer.supplier_id, download='csv'))
        self.assertNotContains(response, 'PSC-B003')
        response = self.client.get(url, dict(mode='best', supplier=self.offer.supplier_id, price_review='1'))
        self.assertContains(response, 'Требует проверки')
        self.assertNotContains(response, 'name="offer_ids"')

    def test_direct_and_bulk_selection_are_blocked_without_mutation(self):
        self.client.force_login(self.user)
        for endpoint, data in [('select', {}), ('select_many', {'offer_ids': [str(self.offer.pk)]})]:
            args = [self.offer.pk] if endpoint == 'select' else []
            response = self.client.post(reverse('procurement:' + endpoint, args=args), data)
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Selection.objects.exists())
        self.audit.delete()
        from .minimum_price_policy import blocked_phaeton_offer_ids
        self.assertEqual(blocked_phaeton_offer_ids([self.offer.pk]), {self.offer.pk})

    def test_recheck_export_includes_brand_alias_and_article_queries(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('procurement:dashboard'),
            dict(mode='best', price_review='1', download='apify', supplier=self.offer.supplier_id))
        data = response.json()
        self.assertEqual(data['searchQueries'], ['PARTS-MALL PSC-B003', 'PSC-B003', 'PMC PARTS MALL PSC-B003'])
        self.assertEqual(data['maxItemsPerQuery'], 5)
        from .apify_cities import APIFY_CITIES
        self.assertEqual(data['cities'], list(APIFY_CITIES))

    def test_alias_finds_lower_card_and_invalidates_approval(self):
        self.approve()
        import_apify_recheck_results([dict(id='153306919', brand='PMC PARTS MALL',
            title='PMC PARTS MALL ролик PSC-B003', url=self.audit.card_url,
            offers=[dict(price=4776)], currency='KZT')], 'pilot.json', self.user)
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.min_price, Decimal('4776'))
        self.assertEqual(self.audit.minimum_review_status, 'needs_review')
        self.assertIsNone(self.audit.minimum_reviewed_at)
        self.assertEqual(self.rows(), [])

    def test_invalid_card_id_and_currency_do_not_change_price(self):
        import_apify_recheck_results([dict(id='000000000', brand='PARTS-MALL',
            title='PARTS-MALL PSC-B003', url=self.audit.card_url, offers=[dict(price=1)])], 'pilot.json', self.user)
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.min_price, Decimal('20000'))

    def test_pilot_keeps_original_prices_and_never_confirms_national_minimum(self):
        from importlib import import_module
        migration = import_module('procurement.migrations.0154_phaeton_pilot_price_observations')
        migration.save_observations(apps, None)
        migration.save_observations(apps, None)
        observation = KaspiPriceAudit.objects.get(source_filename=migration.SOURCE)
        self.audit.refresh_from_db()
        self.saved.refresh_from_db()
        self.assertEqual(self.audit.min_price, Decimal('20000'))
        self.assertEqual(self.saved.kaspi_price_snapshot, Decimal('20000'))
        self.assertEqual(observation.min_price, Decimal('4776'))
        self.assertEqual(observation.minimum_review_evidence['scope'], 'almaty')
        self.assertEqual(self.rows(), [])
