"""Read-only Kaspi/warehouse integration tests, isolated SQLite only."""
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch, Mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from inventory.models import GoodsDocument, Item, StockDocument, StockEntry
from procurement.kaspi_workspace import confirmed_purchase_stock
from procurement.kaspi_materials import pricing
from procurement.models import (
    Supplier, PurchaseOrder, Preferences, KaspiPreparedCard,
    KaspiCardJob,
)


class PurchaseLotIntegrationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('bridge-owner')
        self.supplier = Supplier.objects.create(name='Bridge supplier')
        self.order = PurchaseOrder.objects.create(
            user=self.owner, supplier=self.supplier, request_key=uuid.uuid4(),
            items=[{'brand':'BR','article':'ONE','name':'Filter ONE',
                    'quantity':5,'cost':'1000.00'}],
            estimated_total=Decimal('5000.00'))
        self.item = Item.objects.create(
            code='BRIDGE-1', name='Filter ONE', brand='BR',
            article='ONE', unit='piece')
        self.batch = {'kind':'order','obj':self.order}

    def add_entry(self, kind, quantity, warehouse, purchase=False):
        bundle = GoodsDocument.objects.create(
            kind=kind, date=date.today(), warehouse=warehouse,
            counterparty=self.supplier.name, created_by=self.owner,
            purchase_order=self.order if purchase else None)
        doc = StockDocument.objects.create(
            bundle=bundle, kind=kind, item=self.item, warehouse=warehouse,
            quantity=abs(quantity), unit_cost=Decimal('1000.00'),
            date=date.today(), created_by=self.owner,
            purchase_order=self.order if purchase else None,
            purchase_line=0 if purchase else None, status='posted')
        StockEntry.objects.create(
            document=doc, warehouse=warehouse,
            quantity=quantity, created_by=self.owner)
        return doc

    def test_accounting_admin_sees_other_user_received_purchase_once(self):
        from procurement.kaspi_workspace import purchased_batches
        from procurement.models import Membership
        admin = get_user_model().objects.create_superuser(
            'bridge-accountant', 'bridge-accountant@example.test', 'test-password')
        Membership.objects.create(user=admin, role='admin')
        self.add_entry('receipt', 5, 'PP2', purchase=True)
        batches = purchased_batches(admin)
        self.assertEqual(
            [b['key'] for b in batches if b['key'] == f'order:{self.order.pk}'],
            [f'order:{self.order.pk}'])
        self.assertFalse(any(b['key'].startswith('receipt:') for b in batches))
        self.assertNotIn(f'order:{self.order.pk}',
                         [b['key'] for b in purchased_batches(
                             get_user_model().objects.create_user('unrelated-visitor'))])

    def test_order_quantity_is_not_a_warehouse_balance(self):
        self.assertEqual(confirmed_purchase_stock(
            self.batch,[{'index':0}]), {})

    def test_only_actual_posted_purchase_lot_and_remaining_balance(self):
        self.add_entry('receipt',5,'PP2',purchase=True)
        batch_rows=[{'index':0}]
        stock=confirmed_purchase_stock(self.batch,batch_rows)[0]
        self.assertEqual(stock['stock'],5)
        self.assertEqual(stock['actual_cost'],Decimal('1000'))
        self.add_entry('sale',-2,'PP2')
        self.assertEqual(confirmed_purchase_stock(
            self.batch,batch_rows)[0]['stock'],3)
        other=self.add_entry('receipt',9,'PP2',purchase=False)
        self.assertEqual(confirmed_purchase_stock(
            self.batch,batch_rows)[0]['stock'],3)


class OfficialCatalogClientTests(TestCase):
    @override_settings(KASPI_API_TOKEN='ci-fake-token')
    @patch('procurement.kaspi_catalog_api.requests.request')
    def test_category_read_is_only_get_and_keeps_auth_header_private(self, request):
        from procurement.kaspi_catalog_api import CatalogClient
        request.return_value.status_code = 200
        request.return_value.json.return_value = [{'code':'Master - Test'}]
        self.assertEqual(CatalogClient().categories(), [{'code':'Master - Test'}])
        self.assertEqual(request.call_args.args[0],'GET')
        self.assertEqual(request.call_args.args[1],
                         'https://kaspi.kz/shop/api/products/classification/categories')

    def test_unknown_publication_result_is_not_resent(self):
        user=get_user_model().objects.create_user('bridge-publisher')
        card=KaspiPreparedCard.objects.create(
            identity='bridge-identity',brand='BR',article='ONE',title='Filter ONE')
        KaspiCardJob.objects.create(
            card=card,user=user,kind='publish',status='unknown',payload={})
        from procurement.kaspi_card_jobs import process_card_jobs
        with patch('procurement.kaspi_catalog_api.CatalogClient.submit') as submit:
            self.assertFalse(process_card_jobs())
            submit.assert_not_called()


class KaspiResalePricingTests(TestCase):
    def test_price_floor_uses_received_cost_and_net_profit(self):
        user=get_user_model().objects.create_user('bridge-pricer')
        Preferences.objects.create(user=user,commission=Decimal('12.5'),
                                   expenses=Decimal('250'),best_min_profit=Decimal('1000'))
        card=KaspiPreparedCard(identity='bridge-price',brand='BR',article='ONE',
                               title='Filter ONE')
        result=pricing(user,card,{
            'cost':'1000','actual_cost':Decimal('1200'), 'quantity':5,
        },{'usable':True,'sellers':[{'price':'3000'}]})
        self.assertEqual(result['recommended'],Decimal('2910'))
        self.assertTrue(result['meets_minimum'])
        self.assertGreaterEqual(result['profit'],Decimal('1000'))
