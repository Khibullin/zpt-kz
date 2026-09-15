from datetime import timedelta
from decimal import Decimal
from io import StringIO
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.kaspi_economics import (
    EconomicsReason,
    ExpectedSource,
    calculate_min_price,
    money_ceil_tenge,
    percent_to_fraction,
    report_listing_economics,
    report_product_economics,
    seller_economics,
)
from catalog.models import (
    CatalogImportBatch,
    KaspiEconomicsConfig,
    KaspiOrder,
    KaspiSalesOperation,
    Product,
    ProductKaspiEconomicsPolicy,
    ProductKaspiListing,
    ProductWarehouseStock,
    SellerProfile,
    StockMovement,
    Warehouse,
)
from catalog.stock_service import set_stock_quantity
from catalog.warehouses import WAREHOUSE_CODE_PP1, WAREHOUSE_CODE_PP2


def _seller(username='econ-own'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=username,
        phone='77770000001',
        city='Алматы',
    )


def _product(seller, article, **kwargs):
    defaults = {
        'title': f'Title {article}',
        'article': article,
        'seller_name': seller.name,
        'whatsapp_number': seller.phone,
        'seller_profile': seller,
        'city': seller.city,
        'status': 'active',
        'price': 5900,
        'cost_price': 1000,
        'stock_qty': 9,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _config(seller, packaging='350', handling='450', margin='20.00', active=True):
    return KaspiEconomicsConfig.objects.create(
        seller_profile=seller,
        fulfillment_packaging_per_unit=Decimal(packaging),
        fulfillment_handling_per_unit=Decimal(handling),
        default_min_margin_percent=Decimal(margin),
        is_active=active,
    )


def _order(seller, external_id=None):
    return KaspiOrder.objects.create(
        seller_profile=seller,
        external_order_id=external_id or f'RRN-{uuid4().hex[:10]}',
    )


def _op(seller, order, **kwargs):
    qty = kwargs.get('quantity', 1)
    gross = kwargs.get('gross_amount', Decimal('2000.00'))
    defaults = {
        'seller_profile': seller,
        'order': order,
        'operation_type': KaspiSalesOperation.OperationType.PURCHASE,
        'operation_at': timezone.now(),
        'gross_amount': gross,
        'quantity': qty,
        'unit_gross_amount': abs(gross) / Decimal(qty),
        'match_status': KaspiSalesOperation.MatchStatus.LISTING_MATCHED,
        'source_filename': 'econ.csv',
        'source_sha256': 'abc',
        'source_fingerprint': uuid4().hex,
        'details': 'item',
    }
    defaults.update(kwargs)
    return KaspiSalesOperation.objects.create(**defaults)


def _snapshot(seller):
    return {
        'stocks': list(
            ProductWarehouseStock.objects.order_by('pk').values_list('pk', 'quantity')
        ),
        'moves': list(
            StockMovement.objects.order_by('pk').values_list(
                'pk', 'movement_type', 'quantity_delta'
            )
        ),
        'products': list(
            Product.objects.filter(seller_profile=seller)
            .order_by('pk')
            .values_list('pk', 'price', 'cost_price', 'stock_qty')
        ),
        'listings': list(
            ProductKaspiListing.objects.filter(product__seller_profile=seller)
            .order_by('pk')
            .values_list(
                'pk', 'last_known_our_price', 'last_known_kaspi_qty', 'last_synced_at'
            )
        ),
        'orders': KaspiOrder.objects.filter(seller_profile=seller).count(),
        'operations': KaspiSalesOperation.objects.filter(
            seller_profile=seller
        ).count(),
        'batches': CatalogImportBatch.objects.count(),
        'configs': KaspiEconomicsConfig.objects.count(),
        'policies': ProductKaspiEconomicsPolicy.objects.count(),
    }


class KaspiEconomicsModelTests(TestCase):
    def setUp(self):
        self.seller = _seller()
        self.product = _product(self.seller, 'ART-1')

    def test_config_one_to_one_and_total(self):
        config = _config(self.seller)
        self.assertEqual(config.fulfillment_total_per_unit, Decimal('800.00'))
        self.assertEqual(self.seller.kaspi_economics_config.pk, config.pk)
        with self.assertRaises(Exception):
            _config(self.seller)

    def test_config_rejects_negative_packaging(self):
        config = KaspiEconomicsConfig(
            seller_profile=self.seller,
            fulfillment_packaging_per_unit=Decimal('-1'),
            fulfillment_handling_per_unit=Decimal('450'),
            default_min_margin_percent=Decimal('15'),
        )
        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_policy_one_to_one_and_nullable_fields(self):
        policy = ProductKaspiEconomicsPolicy.objects.create(product=self.product)
        self.assertIsNone(policy.min_margin_percent)
        self.assertIsNone(policy.manual_min_price)
        with self.assertRaises(Exception):
            ProductKaspiEconomicsPolicy.objects.create(product=self.product)

    def test_policy_rejects_negative_manual(self):
        policy = ProductKaspiEconomicsPolicy(
            product=self.product,
            manual_min_price=Decimal('-10'),
        )
        with self.assertRaises(ValidationError):
            policy.full_clean()


class KaspiEconomicsFloorTests(TestCase):
    def test_percent_to_fraction(self):
        self.assertEqual(percent_to_fraction(Decimal('15.00')), Decimal('0.15'))
        self.assertEqual(percent_to_fraction(Decimal('12.5')), Decimal('0.125'))

    def test_floor_formula_and_ceil_rounding(self):
        floor, error = calculate_min_price(
            Decimal('2000'), Decimal('0.10'), Decimal('0.20')
        )
        self.assertIsNone(error)
        self.assertEqual(floor, Decimal('2858'))
        r = Decimal('0.10')
        m = Decimal('0.20')
        c = Decimal('2000')
        self.assertGreaterEqual(floor - floor * r - c, floor * m)
        too_low = floor - Decimal('1')
        self.assertLess(too_low - too_low * r - c, too_low * m)

    def test_example_5137(self):
        floor, error = calculate_min_price(
            Decimal('3750'), Decimal('0.12'), Decimal('0.15')
        )
        self.assertIsNone(error)
        self.assertEqual(floor, Decimal('5137'))

    def test_invalid_when_r_plus_m_at_least_one(self):
        floor, error = calculate_min_price(Decimal('100'), Decimal('0.50'), Decimal('0.50'))
        self.assertIsNone(floor)
        self.assertEqual(error, EconomicsReason.INVALID_MARGIN_CONFIG)
        floor, error = calculate_min_price(Decimal('100'), Decimal('0.60'), Decimal('0.50'))
        self.assertIsNone(floor)
        self.assertEqual(error, EconomicsReason.INVALID_MARGIN_CONFIG)

    def test_floor_rounding_never_rounds_down(self):
        self.assertEqual(money_ceil_tenge(Decimal('2857.0000')), Decimal('2857'))
        self.assertEqual(money_ceil_tenge(Decimal('2857.0001')), Decimal('2858'))
        self.assertEqual(money_ceil_tenge(Decimal('2857.99')), Decimal('2858'))


class KaspiEconomicsHistoryTests(TestCase):
    def setUp(self):
        self.seller = _seller()
        self.product = _product(self.seller, '90915-YZZD2', cost_price=1000)
        self.listing = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku='100500',
            merchant_sku='90915-YZZD2',
            last_known_our_price=5900,
            last_known_kaspi_qty=3,
        )
        self.config = _config(self.seller)
        self.order = _order(self.seller)
        self.pp2 = Warehouse.objects.get(code=WAREHOUSE_CODE_PP2)
        set_stock_quantity(
            product=self.product, warehouse=self.pp2, new_quantity=10, source='test'
        )

    def _purchase(self, **kwargs):
        defaults = {
            'product': self.product,
            'listing': self.listing,
            'quantity': 2,
            'gross_amount': Decimal('4000.00'),
            'commission_amount': Decimal('-100.00'),
            'delivery_cost': Decimal('-1500.00'),
        }
        defaults.update(kwargs)
        return _op(self.seller, self.order, **defaults)

    def test_simple_purchase(self):
        self._purchase()
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.purchase_qty, 2)
        self.assertEqual(row.net_qty, 2)
        self.assertEqual(row.net_gross, Decimal('4000.00'))
        self.assertEqual(row.commission_cost_total, Decimal('100.00'))
        self.assertEqual(row.delivery_cost_total, Decimal('1500.00'))
        self.assertEqual(row.estimated_fulfillment_total, Decimal('1600.00'))
        self.assertEqual(row.estimated_cogs, Decimal('2000.00'))
        self.assertEqual(row.contribution_profit, Decimal('-1200.00'))
        self.assertEqual(row.expected_commission_source, ExpectedSource.PRODUCT_HISTORY)

    def test_commission_refund_nets_to_zero(self):
        self._purchase(quantity=1, gross_amount=Decimal('2000.00'))
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            commission_amount=Decimal('100.00'),
            delivery_cost=Decimal('1500.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.commission_signed_total, Decimal('0.00'))
        self.assertEqual(row.commission_cost_total, Decimal('0.00'))
        self.assertEqual(row.delivery_signed_total, Decimal('0.00'))
        self.assertEqual(row.delivery_cost_total, Decimal('0.00'))
        self.assertEqual(row.net_qty, 0)
        self.assertEqual(row.net_gross, Decimal('0.00'))
        self.assertEqual(row.estimated_fulfillment_total, Decimal('800.00'))
        self.assertIsNone(row.contribution_profit)
        self.assertIsNone(row.contribution_margin)
        self.assertFalse(row.is_historical_profit_ready)
        self.assertIn(EconomicsReason.ZERO_NET_QTY, row.reasons)

    def test_partial_commission_refund(self):
        self._purchase(quantity=1, gross_amount=Decimal('2000.00'))
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            commission_amount=Decimal('30.00'),
        )
        _, rows = report_product_economics(self.seller)
        self.assertEqual(rows[0].commission_cost_total, Decimal('70.00'))

    def test_true_zero_commission_is_valid_zero(self):
        self._purchase(commission_amount=Decimal('0.00'), delivery_cost=Decimal('-100'))
        _, rows = report_product_economics(self.seller)
        self.assertEqual(rows[0].commission_cost_total, Decimal('0.00'))
        self.assertEqual(rows[0].expected_commission_rate, Decimal('0'))
        self.assertEqual(rows[0].expected_commission_source, ExpectedSource.PRODUCT_HISTORY)

    def test_seller_commission_fallback(self):
        self._purchase()
        other = _product(self.seller, 'NEW', cost_price=1000)
        ProductKaspiListing.objects.create(
            product=other, master_sku='8', merchant_sku='NEW', last_known_our_price=4000
        )
        _op(
            self.seller,
            _order(self.seller),
            product=other,
            listing=other.kaspi_listings.get(),
            quantity=1,
            gross_amount=Decimal('3000'),
            commission_amount=None,
            delivery_cost=None,
        )
        _, rows = report_product_economics(self.seller, product_id=other.pk)
        row = rows[0]
        self.assertEqual(row.expected_commission_source, ExpectedSource.SELLER_HISTORY)
        self.assertEqual(row.expected_delivery_source, ExpectedSource.SELLER_HISTORY)

    def test_no_commission_anywhere(self):
        self._purchase(commission_amount=None, delivery_cost=None)
        _, rows = report_product_economics(self.seller)
        self.assertIn(EconomicsReason.NO_COMMISSION_HISTORY, rows[0].reasons)
        self.assertIn(EconomicsReason.NO_DELIVERY_HISTORY, rows[0].reasons)
        self.assertFalse(rows[0].is_calculated_floor_ready)

    def test_delivery_refund_and_purchase_forecast(self):
        self._purchase()
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            commission_amount=Decimal('50.00'),
            delivery_cost=Decimal('500.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.delivery_cost_total, Decimal('1000.00'))
        self.assertEqual(row.expected_delivery_per_unit, Decimal('750.00'))
        self.assertEqual(row.net_qty, 1)

    def test_return_without_financial_refund(self):
        self._purchase(quantity=1, gross_amount=Decimal('2000'))
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            commission_amount=None,
            delivery_cost=None,
        )
        _, rows = report_product_economics(self.seller)
        self.assertEqual(rows[0].commission_cost_total, Decimal('100.00'))
        self.assertEqual(rows[0].net_qty, 0)

    def test_partial_return_qty(self):
        self._purchase()
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            commission_amount=Decimal('50.00'),
            delivery_cost=Decimal('750.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.purchase_qty, 2)
        self.assertEqual(row.return_qty, 1)
        self.assertEqual(row.net_qty, 1)
        self.assertEqual(row.estimated_cogs, Decimal('1000.00'))
        self.assertEqual(row.estimated_fulfillment_total, Decimal('1600.00'))

    def test_return_only_window(self):
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            commission_amount=Decimal('10.00'),
            delivery_cost=Decimal('100.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.purchase_qty, 0)
        self.assertIsNone(row.avg_selling_price)
        self.assertIn(EconomicsReason.NEGATIVE_NET_QTY, row.reasons)

    def test_unmatched_not_in_product_but_in_seller(self):
        self._purchase()
        _op(
            self.seller,
            _order(self.seller),
            product=None,
            listing=None,
            match_status=KaspiSalesOperation.MatchStatus.UNMATCHED,
            quantity=1,
            gross_amount=Decimal('111'),
            commission_amount=Decimal('-5'),
        )
        seller = seller_economics(self.seller)
        self.assertEqual(seller.unmatched, 1)
        self.assertEqual(seller.unmatched_gross, Decimal('111'))
        _, rows = report_product_economics(self.seller, product_id=self.product.pk)
        self.assertEqual(rows[0].purchase_qty, 2)

    def test_ambiguous_not_in_product(self):
        _op(
            self.seller,
            self.order,
            product=None,
            listing=None,
            match_status=KaspiSalesOperation.MatchStatus.AMBIGUOUS_PRODUCT,
            quantity=1,
            gross_amount=Decimal('500'),
        )
        _, rows = report_product_economics(self.seller, product_id=self.product.pk)
        self.assertEqual(rows[0].purchase_qty, 0)

    def test_multiple_listings_product_only(self):
        listing_b = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku='100501',
            merchant_sku='90915-B',
            last_known_our_price=6100,
        )
        self._purchase()
        _op(
            self.seller,
            _order(self.seller),
            product=self.product,
            listing=listing_b,
            quantity=1,
            gross_amount=Decimal('3000.00'),
            commission_amount=Decimal('-90.00'),
            delivery_cost=Decimal('-400.00'),
        )
        _op(
            self.seller,
            _order(self.seller),
            product=self.product,
            listing=None,
            match_status=KaspiSalesOperation.MatchStatus.PRODUCT_ONLY,
            quantity=1,
            gross_amount=Decimal('1000.00'),
            commission_amount=Decimal('-20.00'),
            delivery_cost=Decimal('-100.00'),
        )
        _, products = report_product_economics(self.seller, product_id=self.product.pk)
        product_row = products[0]
        self.assertEqual(product_row.purchase_qty, 4)
        self.assertEqual(product_row.product_only_ops, 1)
        self.assertIsNone(product_row.current_price)
        self.assertIn(EconomicsReason.MULTIPLE_LISTINGS, product_row.reasons)
        _, listing_a = report_listing_economics(self.seller, self.listing.pk)
        _, listing_b_row = report_listing_economics(self.seller, listing_b.pk)
        self.assertEqual(listing_a.purchase_qty, 2)
        self.assertEqual(listing_b_row.purchase_qty, 1)
        self.assertEqual(listing_a.product_only_ops, 1)
        self.assertEqual(listing_a.current_price, Decimal('5900'))
        self.assertEqual(listing_b_row.current_price, Decimal('6100'))
        self.assertEqual(
            listing_a.expected_commission_source, ExpectedSource.LISTING_HISTORY
        )

    def test_product_margin_override_and_manual_floor(self):
        self._purchase()
        ProductKaspiEconomicsPolicy.objects.create(
            product=self.product,
            min_margin_percent=Decimal('30.00'),
            manual_min_price=Decimal('9000.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.min_margin_percent, Decimal('30.00'))
        self.assertGreaterEqual(row.effective_min_price, row.calculated_min_price)
        self.assertEqual(row.effective_min_price, Decimal('9000.00'))

    def test_manual_floor_does_not_lower_calculated(self):
        self._purchase()
        ProductKaspiEconomicsPolicy.objects.create(
            product=self.product,
            manual_min_price=Decimal('100.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.effective_min_price, row.calculated_min_price)
        self.assertGreater(row.effective_min_price, Decimal('100.00'))

    def test_manual_floor_only_when_calculated_unavailable(self):
        ProductKaspiEconomicsPolicy.objects.create(
            product=self.product,
            manual_min_price=Decimal('5500.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertIsNone(row.calculated_min_price)
        self.assertEqual(row.effective_min_price, Decimal('5500.00'))
        self.assertIn(EconomicsReason.MANUAL_FLOOR_ONLY, row.reasons)
        self.assertIn(EconomicsReason.NO_SALES_HISTORY, row.reasons)

    def test_missing_and_inactive_config(self):
        self.config.delete()
        _, rows = report_product_economics(self.seller)
        self.assertIn(EconomicsReason.MISSING_ECONOMICS_CONFIG, rows[0].reasons)
        _config(self.seller, active=False)
        _, rows = report_product_economics(self.seller)
        self.assertIn(EconomicsReason.INACTIVE_CONFIG, rows[0].reasons)

    def test_missing_cost_price(self):
        self.product.cost_price = None
        self.product.save(update_fields=['cost_price'])
        self._purchase()
        _, rows = report_product_economics(self.seller)
        self.assertIn(EconomicsReason.MISSING_COST_PRICE, rows[0].reasons)
        self.assertIsNone(rows[0].contribution_profit)

    def test_below_min_price(self):
        self.listing.last_known_our_price = 100
        self.listing.save(update_fields=['last_known_our_price'])
        self._purchase()
        listing = report_listing_economics(self.seller, self.listing.pk)[1]
        self.assertIn(EconomicsReason.BELOW_MIN_PRICE, listing.reasons)
        self.assertLess(listing.price_headroom, 0)

    def test_null_cost_not_treated_as_zero(self):
        self.product.cost_price = None
        self.product.save(update_fields=['cost_price'])
        _, rows = report_product_economics(self.seller)
        self.assertIsNone(rows[0].cost_price)

    def test_command_zero_writes_and_output(self):
        self._purchase()
        before = _snapshot(self.seller)
        out = StringIO()
        call_command(
            'report_kaspi_economics',
            '--seller-profile-id',
            str(self.seller.pk),
            stdout=out,
        )
        text = out.getvalue()
        self.assertIn('MODE: READ ONLY', text)
        self.assertIn('SELLER HISTORY', text)
        self.assertNotIn('чистая прибыль', text.lower())
        self.assertEqual(_snapshot(self.seller), before)
        listing_out = StringIO()
        call_command(
            'report_kaspi_economics',
            '--seller-profile-id',
            str(self.seller.pk),
            '--listing-id',
            str(self.listing.pk),
            stdout=listing_out,
        )
        listing_text = listing_out.getvalue()
        self.assertIn('LISTING REPORT', listing_text)
        self.assertIn('not allocated to listing', listing_text)
        self.assertIn('estimated_fulfillment_total=1600.00', listing_text)
        self.assertEqual(_snapshot(self.seller), before)

    def test_bulk_query_count_is_bounded(self):
        for index in range(8):
            product = _product(self.seller, f'ART-{index}', cost_price=1000)
            listing = ProductKaspiListing.objects.create(
                product=product,
                master_sku=str(2000 + index),
                merchant_sku=f'ART-{index}',
                last_known_our_price=5000,
            )
            _op(
                self.seller,
                _order(self.seller),
                product=product,
                listing=listing,
                quantity=1,
                gross_amount=Decimal('2000'),
                commission_amount=Decimal('-80'),
                delivery_cost=Decimal('-200'),
            )
        with CaptureQueriesContext(connection) as captured:
            report_product_economics(self.seller)
        self.assertEqual(len(captured), 21)

    def test_does_not_change_warehouse_or_listing_facts(self):
        self._purchase()
        before = _snapshot(self.seller)
        report_product_economics(self.seller)
        report_listing_economics(self.seller, self.listing.pk)
        seller_economics(self.seller)
        self.assertEqual(_snapshot(self.seller), before)
        self.assertEqual(
            ProductWarehouseStock.objects.get(
                product=self.product, warehouse=self.pp2
            ).quantity,
            10,
        )
        self.assertEqual(Warehouse.objects.get(code=WAREHOUSE_CODE_PP1).code, 'PP1')

    def test_commission_over_refund_is_net_credit(self):
        self._purchase(
            quantity=1,
            gross_amount=Decimal('2000.00'),
            commission_amount=Decimal('-100.00'),
            delivery_cost=Decimal('-450.00'),
        )
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            commission_amount=Decimal('120.00'),
            delivery_cost=Decimal('450.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.commission_signed_total, Decimal('20.00'))
        self.assertEqual(row.commission_cost_total, Decimal('-20.00'))
        self.assertNotIn(EconomicsReason.SIGN_INCONSISTENT, row.reasons)

    def test_delivery_over_refund_is_net_credit(self):
        self._purchase(
            quantity=1,
            gross_amount=Decimal('2000.00'),
            commission_amount=Decimal('-100.00'),
            delivery_cost=Decimal('-450.00'),
        )
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            commission_amount=Decimal('100.00'),
            delivery_cost=Decimal('500.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.delivery_signed_total, Decimal('50.00'))
        self.assertEqual(row.delivery_cost_total, Decimal('-50.00'))
        self.assertEqual(row.expected_delivery_per_unit, Decimal('450.00'))

    def test_forecast_commission_uses_purchase_side_not_net(self):
        self._purchase(
            quantity=1,
            gross_amount=Decimal('2000.00'),
            commission_amount=Decimal('-100.00'),
            delivery_cost=Decimal('-1000.00'),
        )
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-2000.00'),
            commission_amount=Decimal('100.00'),
            delivery_cost=Decimal('1000.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.commission_cost_total, Decimal('0.00'))
        self.assertEqual(row.expected_commission_rate, Decimal('0.05'))
        self.assertEqual(row.expected_commission_source, ExpectedSource.PRODUCT_HISTORY)
        self.assertEqual(row.expected_delivery_per_unit, Decimal('1000.00'))

    def test_partial_return_cogs_on_net_qty_fulfillment_on_purchases(self):
        self.product.cost_price = 2000
        self.product.save(update_fields=['cost_price'])
        self._purchase(
            quantity=2,
            gross_amount=Decimal('10000.00'),
            commission_amount=Decimal('-200.00'),
            delivery_cost=Decimal('-400.00'),
        )
        _op(
            self.seller,
            self.order,
            product=self.product,
            listing=self.listing,
            operation_type=KaspiSalesOperation.OperationType.RETURN,
            quantity=1,
            gross_amount=Decimal('-5000.00'),
            commission_amount=Decimal('100.00'),
            delivery_cost=Decimal('200.00'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.net_qty, 1)
        self.assertEqual(row.net_gross, Decimal('5000.00'))
        self.assertEqual(row.estimated_cogs, Decimal('2000.00'))
        self.assertEqual(row.estimated_fulfillment_total, Decimal('1600.00'))
        self.assertEqual(row.commission_cost_total, Decimal('100.00'))
        self.assertEqual(row.delivery_cost_total, Decimal('200.00'))

    def test_true_zero_delivery_is_valid_zero(self):
        self._purchase(delivery_cost=Decimal('0.00'))
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.delivery_cost_total, Decimal('0.00'))
        self.assertEqual(row.expected_delivery_per_unit, Decimal('0'))
        self.assertEqual(row.expected_delivery_source, ExpectedSource.PRODUCT_HISTORY)

    def test_zero_cost_price_is_real_zero(self):
        self.product.cost_price = 0
        self.product.save(update_fields=['cost_price'])
        self._purchase()
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.cost_price, Decimal('0'))
        self.assertEqual(row.estimated_cogs, Decimal('0'))
        self.assertNotIn(EconomicsReason.MISSING_COST_PRICE, row.reasons)
        self.assertTrue(row.is_calculated_floor_ready)

    def test_zero_margin_override_does_not_fallback(self):
        self._purchase()
        ProductKaspiEconomicsPolicy.objects.create(
            product=self.product,
            min_margin_percent=Decimal('0'),
        )
        _, rows = report_product_economics(self.seller)
        row = rows[0]
        self.assertEqual(row.min_margin_percent, Decimal('0'))
        self.assertEqual(row.calculated_min_price, Decimal('2616'))

    def test_listing_falls_back_to_product_history(self):
        self._purchase()
        other = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku='100599',
            merchant_sku='90915-C',
            last_known_our_price=5000,
        )
        _, row = report_listing_economics(self.seller, other.pk)
        self.assertEqual(row.purchase_qty, 0)
        self.assertEqual(row.current_price, Decimal('5000'))
        self.assertEqual(row.expected_commission_source, ExpectedSource.PRODUCT_HISTORY)
        self.assertEqual(row.expected_delivery_source, ExpectedSource.PRODUCT_HISTORY)

    def test_ambiguous_included_in_seller_fallback(self):
        orphan = _product(self.seller, 'ORPHAN', cost_price=1000)
        ProductKaspiListing.objects.create(
            product=orphan,
            master_sku='9',
            merchant_sku='ORPHAN',
            last_known_our_price=4000,
        )
        _op(
            self.seller,
            _order(self.seller),
            product=orphan,
            listing=orphan.kaspi_listings.get(),
            quantity=1,
            gross_amount=Decimal('3000'),
            commission_amount=None,
            delivery_cost=None,
        )
        _op(
            self.seller,
            _order(self.seller),
            product=None,
            listing=None,
            match_status=KaspiSalesOperation.MatchStatus.AMBIGUOUS_PRODUCT,
            quantity=1,
            gross_amount=Decimal('2000'),
            commission_amount=Decimal('-80'),
            delivery_cost=Decimal('-200'),
        )
        _, rows = report_product_economics(self.seller, product_id=orphan.pk)
        row = rows[0]
        self.assertEqual(row.purchase_qty, 1)
        self.assertEqual(row.expected_commission_source, ExpectedSource.SELLER_HISTORY)
        self.assertEqual(row.expected_delivery_source, ExpectedSource.SELLER_HISTORY)
        seller = seller_economics(self.seller)
        self.assertEqual(seller.ambiguous, 1)

    def test_command_date_filters(self):
        self._purchase()
        local = timezone.localtime(timezone.now())
        today = local.strftime('%Y-%m-%d')
        yesterday = (local - timedelta(days=1)).strftime('%Y-%m-%d')
        included = StringIO()
        call_command(
            'report_kaspi_economics',
            '--seller-profile-id',
            str(self.seller.pk),
            '--date-from',
            today,
            '--date-to',
            today,
            stdout=included,
        )
        self.assertIn('Purchases: 1', included.getvalue())
        excluded = StringIO()
        call_command(
            'report_kaspi_economics',
            '--seller-profile-id',
            str(self.seller.pk),
            '--date-from',
            yesterday,
            '--date-to',
            yesterday,
            stdout=excluded,
        )
        self.assertIn('Purchases: 0', excluded.getvalue())
        with self.assertRaises(CommandError):
            call_command(
                'report_kaspi_economics',
                '--seller-profile-id',
                str(self.seller.pk),
                '--date-from',
                today,
                '--date-to',
                yesterday,
                stdout=StringIO(),
            )
        with self.assertRaises(CommandError):
            call_command(
                'report_kaspi_economics',
                '--seller-profile-id',
                str(self.seller.pk),
                '--date-from',
                '15/09/2026',
                stdout=StringIO(),
            )
