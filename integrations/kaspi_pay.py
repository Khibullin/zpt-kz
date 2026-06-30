import json
import uuid
from typing import Any

from django.conf import settings
from django.urls import reverse

from orders.models import KaspiTransaction, Order


class KaspiPayClient:
    """
    Mock Kaspi payment client.

    Replace method bodies with real Kaspi API calls once the bank
    provides merchant credentials and API documentation.
    """

    def __init__(
        self,
        merchant_id: str | None = None,
        api_token: str | None = None,
    ):
        self.merchant_id = merchant_id or getattr(settings, 'KASPI_MERCHANT_ID', '')
        self.api_token = api_token or getattr(settings, 'KASPI_API_TOKEN', '')

    def create_payment_ticket(self, order: Order) -> str:
        """Return the URL where the buyer waits for Kaspi payment."""
        return reverse('orders:order_payment', kwargs={'order_id': order.pk})

    def create_invoice(self, order: Order) -> KaspiTransaction:
        """
        Send invoice to Kaspi app (mock).

        TODO: POST invoice to Kaspi API for order.customer_phone.
        """
        payload = {
            'mode': 'mock',
            'action': 'create_invoice',
            'order_id': order.pk,
            'amount': order.total_price,
            'phone': order.customer_phone,
            'status': 'PENDING',
            'transaction_id': f'MOCK-INV-{uuid.uuid4().hex[:12].upper()}',
            'merchant_id': self.merchant_id or 'mock-merchant',
        }
        return KaspiTransaction.objects.create(
            order=order,
            kaspi_id=payload['transaction_id'],
            status='PENDING',
            raw_response=payload,
        )

    def check_payment_status(self, order: Order) -> bool:
        """
        Poll Kaspi for the current payment status.

        TODO: GET payment status from Kaspi API by kaspi_id.
        """
        return order.status == Order.STATUS_PAID

    def build_mock_success_payload(self, order: Order) -> dict[str, Any]:
        """Helper payload for logging mock callback transactions."""
        return {
            'mode': 'mock',
            'action': 'payment_callback',
            'order_id': order.pk,
            'amount': order.total_price,
            'status': 'SUCCESS',
            'transaction_id': f'MOCK-{uuid.uuid4().hex[:12].upper()}',
            'merchant_id': self.merchant_id or 'mock-merchant',
        }
