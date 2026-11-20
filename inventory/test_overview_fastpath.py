"""Accounting home must calculate summaries only for the eight visible orders."""
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from procurement.models import PurchaseOrder, Supplier


class AccountingOverviewFastPathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='accounting-overview-fastpath', password='test-only')
        supplier = Supplier.objects.create(name='Omega')
        PurchaseOrder.objects.bulk_create([
            PurchaseOrder(
                user=cls.user, supplier=supplier, request_key=uuid.uuid4(),
                estimated_total=Decimal('100'), items=[],
                original_items=[],
            ) for _ in range(15)
        ])

    @patch('inventory.purchasing.order_summary', return_value={
        'delivery_state': 'Ожидается', 'received': 0, 'remaining': 0,
    })
    @patch('inventory.stock_reporting.stock_rows', return_value=[])
    def test_overview_only_summarizes_visible_orders(self, stock_rows, order_summary):
        self.client.force_login(self.user)
        response = self.client.get(reverse('inventory:overview'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['orders']), 8)
        self.assertEqual(order_summary.call_count, 8)
        self.assertEqual(
            [o.pk for o in response.context['orders']],
            sorted([o.pk for o in response.context['orders']], reverse=True))
