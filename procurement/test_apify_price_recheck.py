"""No-production-write regressions for repeated Apify minimum price checks."""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .apify_buying_import import import_apify_results
from .models import (
    KaspiBuyingCandidate, KaspiPriceAudit, Offer,
    PermanentBuyingItem, Preferences, Selection, Supplier,
)


class ApifyMultiCardRecheckTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='apify_price_recheck_user', password='test-only')
        self.omega = Supplier.objects.create(name='Omega')
        Preferences.objects.create(user=self.user,
                                   commission=Decimal('12.5'),
                                   expenses=Decimal('250'))
        self.offer = Offer.objects.create(
            supplier=self.omega, brand='WXQP', article='51331',
            name='Рулевая рейка', category='Рулевое управление',
            cost=Decimal('53214'), stock='4', match_verified=False)
        self.old = PermanentBuyingItem.objects.create(
            offer=self.offer, source='apify', title=self.offer.name,
            product_id='175080373', kaspi_url='https://kaspi.kz/shop/p/first/',
            cost_snapshot=Decimal('53214'),
            kaspi_price_snapshot=Decimal('145000'),
            commission_snapshot=Decimal('12.5'),
            expenses_snapshot=Decimal('250'),
            profit_snapshot=Decimal('73411'),
            roi_snapshot=Decimal('137.9543'),
            identity_status='candidate')
        Selection.objects.create(user=self.user, offer=self.offer, quantity=2)

    def send(self, rows, filename='dataset_kaspi-api_2026-11-20.json'):
        data = json.dumps(rows, ensure_ascii=False).encode('utf8')
        return import_apify_results(
            SimpleUploadedFile(filename, data), filename, user=self.user)

    def card(self, product_id, article, price, *,
             brand='WXQP', query='WXQP 51331', offers=None):
        return {
            'id': product_id, 'title': f'{brand} гидравлическая {article} левый руль 1 шт',
            'brand': brand, 'searchQuery': query, 'inStock': True, 'currency': 'KZT',
            'url': f'https://kaspi.kz/shop/p/recheck-{product_id}/',
            'minPrice': price,
            'offers': ([{'sellerName': 'Дешёвый продавец', 'price': price}]
                       if offers is None else offers),
        }

    def test_duplicate_supplier_sku_is_blocked_and_counted(self):
        # Importing duplicate Omega SKUs with different case can hide stock
        # conflicts. Do not choose the newest or the only positive-stock row.
        Offer.objects.create(
            supplier=self.omega, brand='wxqp', article='51331',
            name='Историческая запись с нулевым остатком',
            category='Рулевое управление', cost=Decimal('53214'),
            stock='0', match_verified=True)
        result = self.send([self.card('155555555', '51331', 78000)])
        self.assertEqual(result['recheck_duplicate_supplier_sku'], 1)
        self.assertEqual(result['recheck_unrelated'], 0)
        self.assertFalse(KaspiPriceAudit.objects.exists())

    def test_leading_zero_is_a_different_full_article(self):
        self.send([self.card('175080373', '51331', 145000)])
        result = self.send([self.card('168038942', '051331', 78000)])
        audit = KaspiPriceAudit.objects.get(offer=self.offer)
        self.assertEqual(result['recheck_matched_cards'], 0)
        self.assertEqual(audit.min_price, Decimal('145000'))
        self.assertEqual(audit.product_id, '175080373')
        self.assertEqual(len(audit.card_ids), 1)
        self.old.refresh_from_db()
        self.assertEqual(self.old.kaspi_price_snapshot, Decimal('145000'))
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.cost, Decimal('53214'))
        self.assertEqual(Selection.objects.get(
            user=self.user, offer=self.offer).quantity, 2)

    def test_search_results_for_other_articles_and_brands_are_ignored(self):
        rows = [
            self.card('111111111', '51335', 100),
            self.card('222222222', '51331', 100, brand='Different'),
            self.card('333333333', '51331', 79000, query='WXQP 70000'),
            self.card('444444444', '51331', 145000),
        ]
        result = self.send(rows)
        self.assertEqual(result['recheck_unrelated'], 2)
        self.assertEqual(result['recheck_price_cards'], 2)
        self.assertEqual(KaspiPriceAudit.objects.get(
            offer=self.offer).min_price, Decimal('79000'))

    def test_cheaper_exact_part_found_under_another_search_query(self):
        # Apify returns card 145316166 for FREY 750102701 during its search
        # for FREY 750101901. The full card identity, not the query, wins.
        other = Offer.objects.create(
            supplier=self.omega, brand='FREY', article='750102701',
            name='Рычаг передний', category='Подвеска',
            cost=Decimal('18908'), stock='7')
        self.send([
            {'id': '145316166', 'brand': 'FREY',
             'title': 'FREY рычаг подвески 750102701 1 шт',
             'searchQuery': 'FREY 750101901', 'inStock': True, 'currency': 'KZT',
             'url': 'https://kaspi.kz/shop/p/frey-rychag-podveski-750102701-145316166/',
             'minPrice': 29999, 'offers': [
                 {'sellerName': 'Seller', 'price': 29999}]},
            {'id': '179823736', 'brand': 'FREY',
             'title': 'FREY рычаг подвески 750102701 1 шт',
             'searchQuery': 'FREY 750102701', 'inStock': True, 'currency': 'KZT',
             'url': 'https://kaspi.kz/shop/p/frey-rychag-podveski-750102701-179823736/',
             'minPrice': 35750, 'offers': [
                 {'sellerName': 'Seller', 'price': 35750}]},
        ])
        audit = KaspiPriceAudit.objects.get(offer=other)
        self.assertEqual(audit.min_price, Decimal('29999'))
        self.assertEqual(audit.product_id, '145316166')
        self.assertEqual(len(audit.card_ids), 2)
        self.assertLess(audit.roi, Decimal('50'))

    def test_seller_minimum_not_card_minprice(self):
        row = self.card('555555555', '51331', 145000, offers=[
            {'sellerName': 'Seller A', 'price': '145000'},
            {'sellerName': 'Seller B', 'price': '78000'},
        ])
        self.send([row])
        self.assertEqual(KaspiPriceAudit.objects.get(
            offer=self.offer).min_price, Decimal('78000'))

    def test_missing_offers_never_creates_a_market_price(self):
        result = self.send([self.card(
            '666666666', '51331', 145000, offers=[])])
        self.assertEqual(result['recheck_missing_sellers'], 1)
        self.assertFalse(KaspiPriceAudit.objects.exists())

    def test_separate_files_preserve_independent_price_snapshots(self):
        self.send([self.card('777777777', '51331', 145000)], 'early.json')
        self.send([self.card('888888888', '51331', 78000)], 'later.json')
        self.assertEqual(KaspiPriceAudit.objects.count(), 2)
        self.assertEqual(KaspiPriceAudit.objects.get(
            source_filename='early.json').min_price, Decimal('145000'))
        self.assertEqual(KaspiPriceAudit.objects.get(
            source_filename='later.json').min_price, Decimal('78000'))

    def test_duplicate_reprocessing_is_idempotent(self):
        row = self.card('999999999', '51331', 78000)
        self.send([row])
        self.send([row])
        self.assertEqual(KaspiPriceAudit.objects.count(), 1)
        self.assertEqual(len(KaspiPriceAudit.objects.get(
            offer=self.offer).card_ids), 1)
        self.assertFalse(KaspiBuyingCandidate.objects.exists())


    def test_springs_are_permitted_with_inclusive_2000_cost(self):
        spring = Offer.objects.create(
            supplier=self.omega, brand='TESTSPR', article='SPR-001',
            name='Пружина подвески', category='Пружины',
            cost=Decimal('2000'), stock='2')
        data = self.card('188888888', 'SPR-001', 4000,
                         brand='TESTSPR', query='TESTSPR SPR-001')
        data['title'] = 'TESTSPR пружина SPR-001 1 шт'
        self.send([data])
        audit = KaspiPriceAudit.objects.get(offer=spring)
        self.assertEqual(audit.min_price, Decimal('4000'))
        self.assertEqual(audit.roi, Decimal('62.5000'))

    def test_below_2000_cost_cannot_enter_new_recheck(self):
        cheap = Offer.objects.create(
            supplier=self.omega, brand='TESTSPR', article='SPR-002',
            name='Пружина подвески', category='Пружины',
            cost=Decimal('1999'), stock='5')
        data = self.card('188888889', 'SPR-002', 5000,
                         brand='TESTSPR', query='TESTSPR SPR-002')
        data['title'] = 'TESTSPR пружина SPR-002 1 шт'
        self.send([data])
        self.assertFalse(KaspiPriceAudit.objects.filter(offer=cheap).exists())

    def test_unknown_kaspi_stock_is_not_positive_confirmation(self):
        data = self.card('188888890', '51331', 90000)
        data.pop('inStock')
        self.send([data])
        self.assertFalse(KaspiPriceAudit.objects.exists())

    def test_malformed_or_unidentified_seller_rejected(self):
        invalid = self.card('133333331', '51331', 79000, offers=[
            {'sellerName':'Valid Seller', 'price':79000},
            {'sellerName':'Missing Price'}])
        self.send([invalid])
        self.assertFalse(KaspiPriceAudit.objects.exists())

    def test_non_single_unit_or_bad_kaspi_url_rejected(self):
        invalid = self.card('133333332', '51331', 79000)
        invalid['title'] = 'WXQP 51331 комплект 2 шт'
        self.send([invalid])
        self.assertFalse(KaspiPriceAudit.objects.exists())
        invalid = self.card('133333333', '51331', 79000)
        invalid['url'] = 'https://example.com/shop/p/recheck-133333333/'
        self.send([invalid])
        self.assertFalse(KaspiPriceAudit.objects.exists())

    def test_minprice_lower_than_observed_sellers_is_unconfirmed(self):
        invalid = self.card('133333334', '51331', 79000,
                            offers=[{'sellerName':'Only seller', 'price':90000}])
        self.send([invalid])
        self.assertFalse(KaspiPriceAudit.objects.exists())

    def test_suffix_not_merged_with_numeric_article(self):
        row = self.card('199999999', '51331-A', 100)
        self.send([row])
        self.assertFalse(KaspiPriceAudit.objects.exists())
